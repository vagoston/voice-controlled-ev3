"""Subscribes to the room's camera track: latest frame for looking, MP4 for recording.

One subscription, two consumers. Encoding runs on a worker thread because
blocking the agent's event loop stalls audio and turn handling.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from livekit import rtc

logger = logging.getLogger("room_video")

TIME_BASE = Fraction(1, 1000)
QUEUE_DEPTH = 120


@dataclass
class RecordingResult:
    path: Path
    seconds: float
    frames: int
    dropped: int
    auto_stopped: bool


class _Encoder:
    """Owns the MP4 container on its own thread, fed by a bounded queue."""

    def __init__(self, path: Path, width: int, height: int) -> None:
        self.path = path
        # h264 needs even dimensions.
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.frames = 0
        self.dropped = 0
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, rgb: bytes, elapsed_ms: int) -> None:
        try:
            self._queue.put_nowait((rgb, elapsed_ms))
        except queue.Full:
            # Dropping a frame is better than stalling the voice loop.
            self.dropped += 1

    def finish(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=15)

    def _run(self) -> None:
        container = av.open(str(self.path), mode="w")
        stream = container.add_stream("libx264", rate=30)
        stream.width = self.width
        stream.height = self.height
        stream.pix_fmt = "yuv420p"
        stream.time_base = TIME_BASE
        stream.options = {"crf": "28", "preset": "veryfast"}

        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                rgb, elapsed_ms = item
                array = np.frombuffer(rgb, dtype=np.uint8).reshape(
                    (self.height, self.width, 3)
                )
                frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                frame.pts = elapsed_ms
                frame.time_base = TIME_BASE
                for packet in stream.encode(frame):
                    container.mux(packet)
                self.frames += 1
            for packet in stream.encode(None):
                container.mux(packet)
        except Exception:
            logger.exception("encoder thread failed")
        finally:
            container.close()


class RoomVideo:
    def __init__(
        self,
        room: rtc.Room,
        recordings_dir: Path,
        max_recording_s: float = 120.0,
    ) -> None:
        self._room = room
        self._dir = recordings_dir
        self._max_s = max_recording_s
        self._latest: rtc.VideoFrame | None = None
        self._task: asyncio.Task | None = None
        self._encoder: _Encoder | None = None
        self._started_at = 0.0
        self._auto_stopped = False
        self._last_result: RecordingResult | None = None
        room.on("track_subscribed", self._on_subscribed)

    def _on_subscribed(self, track: rtc.Track, *_) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO and self._task is None:
            logger.info("room camera track found, consuming frames")
            self._task = asyncio.create_task(self._consume(track))

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track)
        try:
            async for event in stream:
                self._latest = event.frame
                if self._encoder is not None:
                    self._feed(event.frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("video consumer stopped")
        finally:
            await stream.aclose()

    def _feed(self, frame: rtc.VideoFrame) -> None:
        encoder = self._encoder
        if encoder is None:
            return
        elapsed = time.monotonic() - self._started_at
        if elapsed >= self._max_s:
            logger.info("recording hit its %.0fs limit, stopping", self._max_s)
            self._auto_stopped = True
            self._encoder = None  # detach first so no further frames are fed
            asyncio.create_task(self._drain(encoder, elapsed))
            return
        if frame.width < encoder.width or frame.height < encoder.height:
            # Resolution renegotiated mid-recording; skip rather than corrupt.
            encoder.dropped += 1
            return
        rgb = frame.convert(rtc.VideoBufferType.RGB24)
        data = bytes(rgb.data)[: encoder.width * encoder.height * 3]
        encoder.submit(data, int(elapsed * 1000))

    @property
    def has_video(self) -> bool:
        return self._latest is not None

    @property
    def recording(self) -> bool:
        return self._encoder is not None

    def latest_frame(self) -> rtc.VideoFrame | None:
        return self._latest

    def start_recording(self) -> Path:
        if self._encoder is not None:
            raise RuntimeError("already recording")
        if self._latest is None:
            raise RuntimeError("no camera track in the room yet")

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"room-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        self._started_at = time.monotonic()
        self._auto_stopped = False
        self._last_result = None
        self._encoder = _Encoder(path, self._latest.width, self._latest.height)
        logger.info("recording to %s", path)
        return path

    async def _drain(self, encoder: _Encoder, seconds: float) -> RecordingResult:
        # Joining the encoder thread would stall the event loop, so it waits
        # off-thread.
        await asyncio.to_thread(encoder.finish)
        result = RecordingResult(
            path=encoder.path,
            seconds=round(seconds, 1),
            frames=encoder.frames,
            dropped=encoder.dropped,
            auto_stopped=self._auto_stopped,
        )
        self._last_result = result
        return result

    async def stop_recording(self) -> RecordingResult:
        encoder = self._encoder
        if encoder is not None:
            self._encoder = None
            return await self._drain(encoder, time.monotonic() - self._started_at)
        if self._last_result is not None:
            # An auto-stop already finished it; report that rather than erroring.
            return self._last_result
        raise RuntimeError("not recording")

    async def aclose(self) -> None:
        if self._encoder is not None:
            await self.stop_recording()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
