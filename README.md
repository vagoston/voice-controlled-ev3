# Voice-Controlled mBot Rover

Milestone 1 (Agent plumbing): a LiveKit `AgentSession` with STT/LLM/TTS and a
working voice round-trip. No tools/motor control yet.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from your
     [LiveKit Cloud](https://cloud.livekit.io) project (Settings > Keys).
   - `GROQ_API_KEY` from the [Groq console](https://console.groq.com/keys) —
     covers STT (Whisper), LLM (gpt-oss-120b), and TTS (Orpheus) with one key.

2. Activate the venv:
   ```
   .venv\Scripts\activate
   ```

## Scripts (Milestone 1)

- `scripts/test_connectivity.py` (CRY-6) — confirms your LiveKit credentials
  work by minting a token and listing rooms.
- `scripts/test_llm_latency.py` (CRY-8) — rough time-to-first-token check
  comparing `openai/gpt-oss-120b` vs `openai/gpt-oss-20b` on Groq.
- `src/agent.py` (CRY-7) — the basic voice agent. Run with:
  ```
  python src/agent.py dev
  ```
  then connect via the [Agents Playground](https://agents-playground.livekit.io)
  using your LiveKit project, and talk to it.
