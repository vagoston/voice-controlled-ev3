"""Grab one frame from a LiveKit video track and ask the vision model about it.

Prints a join URL, waits for you to publish a camera, then captures a single
frame, saves it next to this script, and sends it to Groq's vision model.

Usage: python scripts/camera_test.py ["your question"]
"""

import argparse
import asyncio
import base64
import os
import urllib.parse
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from livekit import api, rtc
from livekit.agents.utils import images

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

ROOM = "camera-test"
VISION_MODEL = "qwen/qwen3.8-27b"
FRAME_PATH = PROJECT_ROOT / "camera_frame.jpg"
# The join token is a credential, so it goes to a local file and the browser
# directly rather than through anything that displays it.
JOIN_PAGE = PROJECT_ROOT / "camera_join.html"

DEFAULT_QUESTION = (
    "Answer in two sentences. Is there a small LEGO robot visible? "
    "If yes, say where it is in the frame (left, centre or right) and what is near it."
)


def _token(identity: str, *, publish: bool) -> str:
    return (
        api.AccessToken()
        .with_identity(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True, room=ROOM, can_publish=publish, can_subscribe=True
            )
        )
        .to_jwt()
    )


def join_url() -> str:
    lk_url = os.environ["LIVEKIT_URL"]
    query = urllib.parse.urlencode(
        {"liveKitUrl": lk_url, "token": _token("laptop-cam", publish=True)}
    )
    return f"https://meet.livekit.io/custom?{query}"


async def capture_frame(timeout_s: float, settle_s: float = 3.0) -> rtc.VideoFrame | None:
    room = rtc.Room()
    found: asyncio.Queue[rtc.Track] = asyncio.Queue()

    def on_subscribed(track: rtc.Track, *_):
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            found.put_nowait(track)

    room.on("track_subscribed", on_subscribed)
    await room.connect(os.environ["LIVEKIT_URL"], _token("frame-grabber", publish=False))
    print(f"connected to room {ROOM!r}, waiting up to {timeout_s:.0f}s for a camera...")

    # A camera published before we connected won't fire the event.
    for participant in room.remote_participants.values():
        for pub in participant.track_publications.values():
            if pub.track and pub.track.kind == rtc.TrackKind.KIND_VIDEO:
                found.put_nowait(pub.track)

    try:
        track = await asyncio.wait_for(found.get(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await room.disconnect()
        return None

    print(f"video track found, settling for {settle_s:.0f}s before capture...")
    stream = rtc.VideoStream(track)
    frame = None
    first_size = None
    count = 0
    deadline = asyncio.get_running_loop().time() + settle_s
    async for event in stream:
        frame = event.frame
        count += 1
        if first_size is None:
            first_size = (frame.width, frame.height)
        # WebRTC starts on a low-resolution layer and ramps up, so the first
        # frame is not representative.
        if asyncio.get_running_loop().time() >= deadline:
            break
    await stream.aclose()
    await room.disconnect()
    if frame is not None:
        print(
            f"  {count} frames seen: first {first_size[0]}x{first_size[1]}, "
            f"kept {frame.width}x{frame.height}"
        )
    return frame


def ask_vision(jpeg: bytes, question: str) -> str:
    b64 = base64.b64encode(jpeg).decode()
    client = Groq()
    result = client.chat.completions.create(
        model=VISION_MODEL,
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64},
                    },
                ],
            }
        ],
    )
    return result.choices[0].message.content.strip()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--wait", type=float, default=120.0)
    parser.add_argument("--settle", type=float, default=3.0)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="write the join page but leave opening it to you, so you pick the browser",
    )
    opts = parser.parse_args()

    url = join_url()
    JOIN_PAGE.write_text(
        "<!doctype html><meta charset='utf-8'><title>Join camera-test</title>"
        "<body style='font:16px sans-serif;padding:3em'>"
        f"<p><a href=\"{url}\">Click here to join the camera-test room</a></p>"
        "<p>Then press <b>Camera</b> and allow access.</p></body>",
        encoding="utf-8",
    )
    if opts.no_open:
        print(f"open this in your browser: {JOIN_PAGE}")
    else:
        opened = webbrowser.open(url)
        print("browser launched" if opened else f"could not launch a browser - open {JOIN_PAGE}")
    print("enable the Camera button when the page loads\n")

    frame = await capture_frame(opts.wait, opts.settle)
    if frame is None:
        print("No camera track appeared. Open the URL above and enable your camera.")
        return

    jpeg = images.encode(frame, images.EncodeOptions(format="JPEG", quality=85))
    FRAME_PATH.write_bytes(jpeg)
    print(f"captured {frame.width}x{frame.height}, {len(jpeg) // 1024} KB -> {FRAME_PATH}")

    print(f"\nasking {VISION_MODEL}: {opts.question}\n")
    print("MODEL:", ask_vision(jpeg, opts.question))


if __name__ == "__main__":
    asyncio.run(main())
