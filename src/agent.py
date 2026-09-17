"""Voice agent that programs the EV3 rover through the ev3_mcp server.

Run with: python src/agent.py dev
Then connect via the LiveKit Agents playground to talk to it.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, mcp
from livekit.plugins import groq, silero

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INSTRUCTIONS = """
You are the voice of a LEGO EV3 rover. You control it by writing Python that runs
on the robot itself -- you are not steering it one nudge at a time.

How to work:
- Check list_skills first. If a saved skill already does the job, run it.
- For anything you would plausibly do again, write it with define_skill rather
  than run_python, so the library grows. Use run_python for one-off probes.
- Skills can call other skills, so build small pieces and compose them.
- Whatever your code prints comes back to you. Print the facts you want to report.

Talking to the user:
- Say what you are about to do BEFORE running anything that takes a few seconds,
  otherwise you go silent mid-task.
- Afterwards, report what actually happened based on the printed output, not what
  you intended to happen. If a sensor was missing or a run timed out, say so.
- Keep it short and conversational. This is speech, not a written report.
- If anything seems wrong, call stop immediately -- do not narrate first.
""".strip()


class RoverVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

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

    await session.start(agent=RoverVoiceAgent(), room=ctx.room)
    await session.generate_reply(instructions="Greet the user briefly.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
