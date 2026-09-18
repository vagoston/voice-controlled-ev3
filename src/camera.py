"""Captures the laptop webcam and publishes it into the LiveKit room.

Replaces joining from a browser tab. Capture runs on its own thread because
decoding blocks, and blocking the agent's event loop stalls audio and turn
handling.

Uses PyAV, which is already a livekit-agents dependency and is what encodes
recordings, so no extra dependency. It also selects the camera by *name*, which
matters on laptops that expose a second infrared camera for Windows Hello --
index-based selection can silently grab that one.

An SFU never echoes a track back to its publisher, so frames are handed to the
local consumer directly via `on_frame` rather than arriving by subscription.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from typing import Callable

import av
from livekit import rtc

logger = logging.getLogger("camera")

TRACK_NAME = "laptop-camera"


def default_device() -> tuple[str, str]:
    """(device spec, input format) for this platform."""
    configured = os.getenv("EV3_CAMERA_DEVICE", "").strip()
    if sys.platform == "win32":
        name = configured or "Integrated Camera"
        return f"video={name}", "dshow"
    if sys.platform == "darwin":
        return configured or "0", "avfoundation"
    return configured or "/dev/video0", "v4l2"


class LocalCamera:
    def __init__(
        self,
        on_frame: Callable[[rtc.VideoFrame], None] | None = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 15,
    ) -> None:
        self._on_frame = on_frame
        self._width = width
        self._height = height
        self._fps = fps

        self._container: av.container.InputContainer | None = None
        self._source: rtc.VideoSource | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, room: rtc.Room) -> bool:
        """Open the camera and publish it. False when there is no usable camera."""
        spec, fmt = default_device()
        options = {
            "video_size": f"{self._width}x{self._height}",
            "framerate": str(self._fps),
        }
        try:
            container = await asyncio.to_thread(
                lambda: av.open(spec, format=fmt, options=options)
            )
        except Exception as exc:
            logger.info(
                "no usable camera at %r (%s): %s. "
                "Set EV3_CAMERA_DEVICE if yours has a different name.",
                spec,
                fmt,
                exc,
            )
            return False

        stream = container.streams.video[0]
        self._container = container
        self._loop = asyncio.get_running_loop()
        self._source = rtc.VideoSource(stream.width, stream.height)

        track = rtc.LocalVideoTrack.create_video_track(TRACK_NAME, self._source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        )

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("publishing %s at %dx%d", spec, stream.width, stream.height)
        return True

    def _run(self) -> None:
        try:
            for decoded in self._container.decode(video=0):
                if self._stop.is_set():
                    break
                array = decoded.to_ndarray(format="rgb24")
                height, width = array.shape[:2]
                frame = rtc.VideoFrame(
                    width, height, rtc.VideoBufferType.RGB24, array.tobytes()
                )
                if self._loop is not None and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._publish, frame)
        except Exception:
            if not self._stop.is_set():
                logger.exception("camera capture stopped")

    def _publish(self, frame: rtc.VideoFrame) -> None:
        if self._source is not None:
            self._source.capture_frame(frame)
        if self._on_frame is not None:
            self._on_frame(frame)

    async def aclose(self) -> None:
        self._stop.set()
        if self._container is not None:
            # Closing unblocks the decode loop the thread is sitting in.
            self._container.close()
            self._container = None
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5)
            self._thread = None
