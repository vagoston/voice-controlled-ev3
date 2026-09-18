"""End-to-end check of the laptop camera path, with no browser involved.

Opens the camera, publishes it to a LiveKit room, confirms frames come back
through RoomVideo, and records a short clip.

Usage: python scripts/camera_selftest.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api, rtc

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.camera import LocalCamera, default_device  # noqa: E402
from src.room_video import RoomVideo  # noqa: E402

ROOM = "camera-selftest"


async def main() -> None:
    print("device:", default_device())

    token = (
        api.AccessToken()
        .with_identity("camera-selftest")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM, can_publish=True))
        .to_jwt()
    )
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    print(f"connected to room {ROOM!r}")

    video = RoomVideo(room, PROJECT_ROOT / "recordings", max_recording_s=30.0)
    camera = LocalCamera(on_frame=video.submit_local_frame)

    if not await camera.start(room):
        print("FAIL: no camera could be opened")
        await room.disconnect()
        return

    print("camera published, waiting for frames...")
    await asyncio.sleep(2.0)
    print(f"  status={video.status}  available={video.available}")

    frame = video.latest_frame()
    if frame is None:
        print("FAIL: no frames reached RoomVideo")
    else:
        print(f"  frame reaching RoomVideo: {frame.width}x{frame.height}")

    print("\nrecording 3 seconds...")
    path = video.start_recording()
    await asyncio.sleep(3.0)
    result = await video.stop_recording()
    print(
        f"  saved={result.saved} frames={result.frames} dropped={result.dropped} "
        f"seconds={result.seconds}"
    )
    if result.saved:
        print(f"  {path.name}: {path.stat().st_size // 1024} KB")

    await camera.aclose()
    await video.aclose()
    await room.disconnect()
    print("\nclosed cleanly")


if __name__ == "__main__":
    asyncio.run(main())
