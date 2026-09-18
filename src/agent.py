"""Voice agent that programs the EV3 rover and watches the room through the laptop camera.

Run with: python src/agent.py dev
Then connect via the LiveKit Agents playground to talk to it.
"""

import base64
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    function_tool,
    mcp,
)
from livekit.agents.utils import images
from livekit.plugins import groq, silero

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.room_video import RoomVideo  # noqa: E402

logger = logging.getLogger("agent")

VISION_MODEL = "qwen/qwen3.8-27b"
RECORDINGS_DIR = PROJECT_ROOT / "recordings"

INSTRUCTIONS = """
You are the voice of a LEGO EV3 rover. You control it by writing Python that runs
on the robot itself -- you are not steering it one nudge at a time. A fixed camera
in the room also lets you see the robot from outside.

How to work:
- Check list_skills first. If a saved skill already does the job, run it.
- For anything you would plausibly do again, write it with define_skill rather
  than run_python, so the library grows. Use run_python for one-off probes.
- Skills can call other skills, so build small pieces and compose them.
- Whatever your code prints comes back to you. Print the facts you want to report.

Using the camera:
- look() answers a question about what the camera sees right now. Ask something
  narrow -- "which side of the room is the robot on" beats "describe the scene",
  because open-ended questions invite invention.
- The camera is a second opinion, not proof. If it disagrees with what the robot's
  sensors reported, say both rather than picking a winner.
- Only start_recording when the user asks. Tell them where it was saved afterwards.

Talking to the user:
- Say what you are about to do BEFORE running anything that takes a few seconds,
  otherwise you go silent mid-task.
- Afterwards, report what actually happened based on the printed output, not what
  you intended to happen. If a sensor was missing or a run timed out, say so.
- Keep it short and conversational. This is speech, not a written report.
- If anything seems wrong, call stop immediately -- do not narrate first.
""".strip()


def build_camera_tools(video: RoomVideo) -> list:
    @function_tool(
        name="look",
        description=(
            "Look through the room camera and answer a question about what is "
            "visible right now. Keep the question narrow and concrete."
        ),
    )
    async def look(question: str) -> str:
        frame = video.latest_frame()
        if frame is None:
            return "No camera is publishing to the room, so I cannot see anything."

        jpeg = images.encode(frame, images.EncodeOptions(format="JPEG", quality=85))
        reply = Groq().chat.completions.create(
            model=VISION_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64,"
                                + base64.b64encode(jpeg).decode()
                            },
                        },
                    ],
                }
            ],
        )
        return reply.choices[0].message.content.strip()

    @function_tool(
        name="start_recording",
        description="Start recording the room camera to a video file. Only when asked.",
    )
    async def start_recording() -> str:
        try:
            path = video.start_recording()
        except RuntimeError as exc:
            return f"Could not start recording: {exc}"
        return f"Recording to {path.name}. It stops automatically if left running."

    @function_tool(
        name="stop_recording",
        description="Stop recording the room camera and report where the file was saved.",
    )
    async def stop_recording() -> str:
        try:
            result = await video.stop_recording()
        except RuntimeError as exc:
            return f"Could not stop recording: {exc}"
        note = " (it had already hit its time limit)" if result.auto_stopped else ""
        dropped = f", {result.dropped} frames dropped" if result.dropped else ""
        return (
            f"Saved {result.path.name}: {result.seconds:.0f} seconds, "
            f"{result.frames} frames{dropped}{note}."
        )

    return [look, start_recording, stop_recording]


class RoverVoiceAgent(Agent):
    def __init__(self, tools: list) -> None:
        super().__init__(instructions=INSTRUCTIONS, tools=tools)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    video = RoomVideo(ctx.room, RECORDINGS_DIR)
    ctx.add_shutdown_callback(video.aclose)

    ev3_server = mcp.MCPServerStdio(
        command=sys.executable,
        args=["-m", "ev3_mcp.server"],
        cwd=str(PROJECT_ROOT),
        # Skills drive the robot for seconds at a time; the 5s default would
        # abort them mid-run.
        client_session_timeout_seconds=90,
    )

    session = AgentSession(
        stt=groq.STT(),
        llm=groq.LLM(model="openai/gpt-oss-120b"),
        tts=groq.TTS(),
        vad=silero.VAD.load(),
        mcp_servers=[ev3_server],
        # list_skills -> get_skill -> define_skill -> run_skill is four hops.
        max_tool_steps=10,
    )

    await session.start(agent=RoverVoiceAgent(build_camera_tools(video)), room=ctx.room)
    await session.generate_reply(instructions="Greet the user briefly.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
