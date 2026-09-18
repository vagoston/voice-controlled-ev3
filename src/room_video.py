"""Subscribes to the room's camera track: latest frame for looking, MP4 for recording.

One subscription, two consumers. Encoding runs on a worker thread because
blocking the agent's event loop stalls audio and turn handling.

The camera is optional and can come and go mid-session, so availability is
derived from frame freshness rather than from ever having seen a frame -- a
stale frame is worse than no frame, because it gets described as the present.
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
FRESH_WINDOW_S = 5.0


@dataclass
class RecordingResult:
    path: Path
    seconds: float
    frames: int
    dropped: int
    auto_stopped: bool
    camera_lost: bool
    saved: bool
    """False when nothing was captured. ffmpeg writes no file for zero frames."""


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
        fresh_window_s: float = FRESH_WINDOW_S,
    ) -> None:
        self._dir = recordings_dir
        self._max_s = max_recording_s
        self._fresh_s = fresh_window_s

        self._latest: rtc.VideoFrame | None = None
        self._latest_at = 0.0
        self._task: asyncio.Task | None = None

        self._encoder: _Encoder | None = None
        self._started_at = 0.0
        self._auto_stopped = False
        self._camera_lost = False
        self._auto_task: asyncio.Task | None = None
        self._last_result: RecordingResult | None = None

        room.on("track_subscribed", self._on_subscribed)
        room.on("track_unsubscribed", self._on_unsubscribed)

    # --- availability ------------------------------------------------------

    @property
    def status(self) -> str:
        """``live``, ``stalled`` (track up but no recent frames), or ``off``."""
        if self._latest is None:
            return "off"
        if time.monotonic() - self._latest_at > self._fresh_s:
            return "stalled"
        return "live"

    @property
    def available(self) -> bool:
        return self.status == "live"

    @property
    def recording(self) -> bool:
        return self._encoder is not None

    def latest_frame(self) -> rtc.VideoFrame | None:
        """The most recent frame, or None when it is missing or too old to trust."""
        return self._latest if self.available else None

    # --- track lifecycle ---------------------------------------------------

    def _on_subscribed(self, track: rtc.Track, *_) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO or self._task is not None:
            return
        logger.info("room camera track found, consuming frames")
        self._task = asyncio.create_task(self._consume(track))

    def _on_unsubscribed(self, track: rtc.Track, *_) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        logger.info("room camera went away")
        self._drop_camera()

    def _drop_camera(self) -> None:
        self._latest = None
        self._latest_at = 0.0
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._encoder is not None:
            # Frames have stopped, so nothing else would ever close the file.
            logger.info("camera lost mid-recording, finalising what we have")
            self._camera_lost = True
            encoder, self._encoder = self._encoder, None
            asyncio.create_task(self._drain(encoder, time.monotonic() - self._started_at))

    def _accept(self, frame: rtc.VideoFrame) -> None:
        self._latest = frame
        self._latest_at = time.monotonic()
        if self._encoder is not None:
            self._feed(frame)

    def submit_local_frame(self, frame: rtc.VideoFrame) -> None:
        """Frames from a camera this process publishes.

        An SFU never echoes a track back to its publisher, so these arrive here
        directly instead of through track_subscribed.
        """
        self._accept(frame)

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track)
        try:
            async for event in stream:
                self._accept(event.frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("video consumer stopped")
            self._drop_camera()
        finally:
            await stream.aclose()

    def _feed(self, frame: rtc.VideoFrame) -> None:
        encoder = self._encoder
        if encoder is None:
            return
        if frame.width < encoder.width or frame.height < encoder.height:
            # Resolution renegotiated mid-recording; skip rather than corrupt.
            encoder.dropped += 1
            return
        # convert() rejects a no-op conversion, so only call it when needed.
        if frame.type == rtc.VideoBufferType.RGB24:
            rgb = frame
        else:
            rgb = frame.convert(rtc.VideoBufferType.RGB24)
        data = bytes(rgb.data)[: encoder.width * encoder.height * 3]
        encoder.submit(data, int((time.monotonic() - self._started_at) * 1000))

    # --- recording ---------------------------------------------------------

    def start_recording(self) -> Path:
        if self._encoder is not None:
            raise RuntimeError("already recording")
        frame = self.latest_frame()
        if frame is None:
            raise RuntimeError(f"the room camera is {self.status}")

        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"room-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        self._started_at = time.monotonic()
        self._auto_stopped = False
        self._camera_lost = False
        self._last_result = None
        self._encoder = _Encoder(path, frame.width, frame.height)
        # Time-driven, not frame-driven: if the camera stops sending, a
        # frame-driven limit would never fire.
        self._auto_task = asyncio.create_task(self._auto_stop())
        logger.info("recording to %s", path)
        return path

    async def _auto_stop(self) -> None:
        await asyncio.sleep(self._max_s)
        if self._encoder is None:
            return
        logger.info("recording hit its %.0fs limit, stopping", self._max_s)
        self._auto_stopped = True
        encoder, self._encoder = self._encoder, None
        await self._drain(encoder, time.monotonic() - self._started_at)

    async def _drain(self, encoder: _Encoder, seconds: float) -> RecordingResult:
        # Joining the encoder thread would stall the event loop, so it waits
        # off-thread.
        await asyncio.to_thread(encoder.finish)
        saved = encoder.frames > 0 and encoder.path.exists()
        if not saved:
            encoder.path.unlink(missing_ok=True)
        result = RecordingResult(
            path=encoder.path,
            seconds=round(seconds, 1),
            frames=encoder.frames,
            dropped=encoder.dropped,
            auto_stopped=self._auto_stopped,
            camera_lost=self._camera_lost,
            saved=saved,
        )
        self._last_result = result
        return result

    async def stop_recording(self) -> RecordingResult:
        if self._auto_task is not None:
            self._auto_task.cancel()
            self._auto_task = None

        encoder = self._encoder
        if encoder is not None:
            self._encoder = None
            return await self._drain(encoder, time.monotonic() - self._started_at)
        if self._last_result is not None:
            # An auto-stop or a lost camera already finished it; report that
            # rather than erroring.
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
