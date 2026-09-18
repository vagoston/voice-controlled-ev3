# Voice-Controlled EV3

A LiveKit voice agent that controls a LEGO Mindstorms EV3 rover by **writing
Python that runs on the robot**, rather than steering it one command at a time.

Talk to it, and it composes code, saves reusable skills, runs them on the brick,
and reports back what actually happened.

## Architecture

Three agents with different strengths:

| Layer | Role |
|---|---|
| You | High-level goals, spoken |
| LLM (Groq `gpt-oss-120b`) | Judgement, code authoring, reporting — slow feedback loop |
| EV3 brick (ev3dev) | Fast reactive execution — tight sensor loops at full speed |

The MCP server ships Python source to the brick over SSH (base64 through
`micropython -c`), so a wall-following loop runs *on the robot* at its own speed
instead of round-tripping through the model. Whatever the program prints comes
back as the tool result.

Tools are split along one seam: **MCP is the robot, native agent tools are the
room.** The MCP server SSHes to a brick and has no business holding a camera.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from
     [LiveKit Cloud](https://cloud.livekit.io) (Settings > Keys).
   - `GROQ_API_KEY` from the [Groq console](https://console.groq.com/keys) —
     covers STT (Whisper), LLM, and TTS with one key.
   - `EV3_HOST` / `EV3_USER` / `EV3_PASSWORD` for the brick. ev3dev's defaults
     are `robot` / `maker`, reachable at `ev3dev.local`.

2. Install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

**`EV3_DRY_RUN=1` develops without hardware** — tools return the program they
would have run instead of running it.

Note that LiveKit Cloud may have **agent session recording enabled server-side**,
in which case session audio, transcript, traces and logs upload to your LiveKit
project by default. `AgentSession.start(record=False)` turns it off, or pass a
dict like `record={"audio": False}` for finer control. This is separate from the
room camera recording below, which is local and only runs when asked.

## Skills

Skills are `.py` files in `skills/`, one per skill, each defining a function
matching its filename. They live on the host rather than the brick's SD card, so
they survive reflashes and stay reviewable. All skills are shipped together on
every run, so they can call each other.

The model manages them through `define_skill`, `get_skill`, `list_skills`,
`run_skill` and `delete_skill`. Skills are written against the helpers in
`ev3_mcp/brick_api.py` (`drive`, `obstacle_cm`, `touch_pressed`, `color`,
`motor`, `wheel_degrees`, …).

Brick-side code runs under **MicroPython (Python 3.4)** — no f-strings.

## Room camera

A fixed camera watches the room, giving the agent a third-person view of the
robot — an external observer rather than robot vision, so it can check what the
robot *claims* against what actually happened.

The agent publishes the laptop camera into the room itself, and everything reads
that one track: your phone renders it, the agent samples frames, the recorder
encodes them. Nothing opens the webcam twice, and no browser tab is involved.

Capture uses PyAV — already a dependency, since it also encodes the recordings —
and selects the camera **by name**, which matters on laptops that expose a second
infrared camera for Windows Hello. Index-based selection can silently grab that
one and hand the vision model a greyscale IR image. Override with
`EV3_CAMERA_DEVICE` if yours is not called `Integrated Camera`.

There is no enable/disable setting: whether the camera is usable is a physical
decision, not a config flag, and certainly not an agent-controllable tool. Note
that **covering the lens is not the same as the camera being off** — frames keep
flowing, just dark, so the agent will describe a dark image rather than reporting
no camera.

Three native agent tools:

- `look(question)` — sends the latest frame to `qwen/qwen3.8-27b` (same Groq key)
  and returns its answer.
- `start_recording()` / `stop_recording()` — writes MP4 to `recordings/` and
  reports path, duration and frame count.

Recording is local rather than LiveKit Egress. Egress runs on LiveKit's servers,
so on Cloud it writes to S3/GCS/Azure — "record to the laptop" would mean a round
trip out to a bucket and back. PyAV ships with `livekit-agents` anyway.

Two things learned the hard way, both encoded in the implementation:

- **Never use the first frame.** WebRTC starts on a low-resolution layer: the
  first frame measured 384×216 where the settled one was 1280×720.
- **Ask narrow questions.** On a synthetic test image the model invented a detail
  that wasn't there. On a real photo it was accurate on every claim. Open-ended
  prompts invite narration; treat the camera as a second opinion, not an oracle.

Encoding runs on a worker thread behind a bounded queue that drops frames rather
than blocking, because stalling the event loop delays audio and turn handling.
Recordings auto-stop at a time limit for the same reason motor runs do — a
forgotten recording just fills a disk instead of hitting a wall.

`python scripts/camera_selftest.py` checks the whole path — open, publish,
consume, record — without needing the agent or a browser.

## Guardrails

Bounded runtime is the only real one, and it exists because the failure that
actually happens is a loop that never exits. Two tiers:

- **Brick-side budget** — the program can check `time_left()` / `out_of_time()`
  and exit cleanly, so partial results still come back. Durations are clamped to
  whatever budget remains.
- **Host-side watchdog** — kills the run and forces a stop. No useful output, but
  it is the guarantee.

`_safe_stop()` is appended to every program, and the host forces a stop after any
crash or timeout, because ev3dev motor state lives in sysfs and outlives the
process that set it. **Motors never persist across tool calls** — continuous
motion belongs inside a single program.

There is deliberately no speed cap. It prevented nothing real (80% is plenty fast
to drive off a table) while implying a safety it did not provide. Speed is bounded
to ±100 only because that is the hardware's actual range. These are guardrails
against mistakes, not enforcement — the physical buttons remain the backstop.

## Running

```
python src/agent.py dev
```

Then connect via the [Agents Playground](https://agents-playground.livekit.io)
and talk to it.

To run a skill directly, without the voice layer:

```
python scripts/run_skill.py hardware_check '{"wait_s": 40}' --timeout 55
```

## Scripts

- `scripts/run_skill.py` — run any saved skill against the robot.
- `scripts/camera_selftest.py` — open, publish, consume and record the camera,
  end to end, with no browser.
- `scripts/camera_test.py` — grab one frame and ask the vision model about it.
  Saves the frame so you can see what it was given. Publishes from a browser
  tab, so it also works when the camera is on another machine.
- `scripts/test_mcp_server.py` — spawn the MCP server over stdio and list its tools.
- `scripts/test_connectivity.py` — verify LiveKit credentials.
- `scripts/test_llm_latency.py` — time-to-first-token for Groq models.

## Hardware as tested

An EV3D4 build. This layout is currently hardcoded in env vars and assumed by
`brick_api`; moving it into a declarative profile is still to do.

Sensors are mode-based — one mode at a time, and switching costs ~15 ms on the
colour sensor, ~45 ms on the IR sensor.

| Port | Device |
|---|---|
| outA | Medium motor (head) |
| outB / outC | Large motors (drive) |
| in1 | Touch sensor |
| in3 | Colour sensor |
| in4 | IR sensor (proximity, and beacon seek) |

No ultrasonic or gyro on this build; `distance_cm()` and `gyro_angle()` return
`None`, and `obstacle_cm()` falls back to scaled IR proximity.
