"""CRY-7: basic AgentSession with STT/LLM/TTS, no tools/motor control yet.

Run with: python src/agent.py dev
Then connect via the LiveKit Agents playground to talk to it.
"""

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import anthropic, cartesia, deepgram, silero

load_dotenv()


class RoverVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are the voice interface for a small robot rover. "
                "For now you can only talk -- you have no tools to move the "
                "rover yet. Keep responses short and conversational."
            )
        )


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=anthropic.LLM(model="claude-haiku-4-5-20251001"),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )

    await session.start(agent=RoverVoiceAgent(), room=ctx.room)
    await session.generate_reply(instructions="Greet the user briefly.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
