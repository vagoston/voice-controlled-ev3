# Voice-Controlled mBot Rover

Milestone 1 (Agent plumbing): a LiveKit `AgentSession` with STT/LLM/TTS and a
working voice round-trip. No tools/motor control yet.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from your
     [LiveKit Cloud](https://cloud.livekit.io) project (Settings > Keys).
   - `ANTHROPIC_API_KEY` from the [Anthropic console](https://console.anthropic.com).
   - `DEEPGRAM_API_KEY` from [Deepgram](https://console.deepgram.com).
   - `CARTESIA_API_KEY` from [Cartesia](https://play.cartesia.ai).

2. Activate the venv:
   ```
   .venv\Scripts\activate
   ```

## Scripts (Milestone 1)

- `scripts/test_connectivity.py` (CRY-6) — confirms your LiveKit credentials
  work by minting a token and listing rooms.
- `scripts/test_llm_latency.py` (CRY-8) — rough time-to-first-token check for
  candidate LLMs.
- `src/agent.py` (CRY-7) — the basic voice agent. Run with:
  ```
  python src/agent.py dev
  ```
  then connect via the [Agents Playground](https://agents-playground.livekit.io)
  using your LiveKit project, and talk to it.

STT/TTS providers (Deepgram/Cartesia) are placeholder picks to get the
round-trip working — swap freely.
