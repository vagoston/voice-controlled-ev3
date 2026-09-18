# Voice-Controlled EV3

A LiveKit voice agent that drives a LEGO Mindstorms EV3. You speak to it, it
writes Python, the robot runs that Python, and the agent reports what the
program printed.

## Architecture

| Layer | Job |
|---|---|
| You | Say what you want done |
| LLM (Groq `gpt-oss-120b`) | Decide what to do, write the code, report back |
| EV3 brick (ev3dev) | Run the code, including tight sensor loops |

The MCP server base64-encodes Python source and pipes it to `micropython -c`
over SSH. A wall-following loop therefore runs on the brick at full speed
instead of one tool call per motor command. The program's stdout is returned as
the tool result.

Robot tools come from the MCP server. Camera tools are native agent tools,
because the camera is attached to the laptop, not the robot.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from
     [LiveKit Cloud](https://cloud.livekit.io), under Settings > Keys.
   - `GROQ_API_KEY` from the [Groq console](https://console.groq.com/keys). One
     key covers STT, LLM and TTS.
   - `EV3_HOST`, `EV3_USER`, `EV3_PASSWORD`. ev3dev ships with `robot` / `maker`
     and announces itself as `ev3dev.local`.

2. Install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

Every command below uses `.venv\Scripts\python.exe`. Bare `python` is the system
interpreter and will fail on `paramiko`. Run `.venv\Scripts\activate` once if you
prefer typing `python`.

Set `EV3_DRY_RUN=1` to work without the robot. Tools then return the program
they would have run.

Port assignments are not environment variables. They live in `robot.toml`.

LiveKit Cloud may enable agent session recording at the project level. When it
is on, session audio, transcripts, traces and logs upload to your LiveKit
project. Turn it off with `AgentSession.start(record=False)`, or pass something
like `record={"audio": False}` for partial control. This is unrelated to the
camera recording described below.

## Hardware profile

`robot.toml` defines which motor and sensor is on which port. Edit it after
rebuilding the robot.

```toml
[motors]
left = { port = "B", kind = "large" }
right = { port = "C", kind = "large" }
head = { port = "A", kind = "medium", note = "Starting position is unknown..." }

[sensors]
touch = { input = 1 }
color = { input = 3, note = "Needs to be within about 1cm of a surface..." }

[geometry]
wheel_diameter_mm = 30
axle_track_mm = 140
```

Every motor is named. `left` and `right` are ordinary names that `drive()`
happens to require; a robot without them loads fine, and `drive()`, `forward()`,
`stop()` and the other tank helpers report what motors do exist instead.

A motor or sensor can be written as a bare port (`head = "A"`, `touch = 1`) when
there is nothing else to say about it.

`note` fields are sent to the agent. Use them for anything that cannot be worked
out from the wiring: a sensor's usable range, a motor whose starting position is
unknown. `#` comments are for the human reader and the agent never sees them.

Three things use the profile:

- `brick_api` opens sensors at their configured input. A sensor the profile does
  not list is reported as absent instead of probed for.
- Skills address motors by name: `motor("head", 30, 0.5)`.
- The model is given the layout, the notes, and the geometry in its tool
  descriptions, so it does not have to guess what is fitted.

With `[geometry]` set, skills can move in real units: `drive_cm(20)` and
`turn(90)` work from encoder counts rather than guessed durations. Both return
what the encoders measured, so a skill can compare that against what it asked
for. Without geometry those calls refuse, and the agent is told to use durations
and call them approximate.

`reversed = true` on a motor flips its positive direction, which is needed when
drive motors are mirror-mounted. Run `calibrate_movement` to check: if
`drive(50, 50)` spins instead of driving, set it on one motor; if the robot
drives backwards, set it on both.

`list_devices` compares the profile against what the brick reports and lists any
differences.

Bad configurations fail at load: duplicate drive ports, a named motor reusing a
drive port, unknown sensor types, inputs outside 1-4, two sensors on one input.

EV3 sensors hold one mode at a time. Switching modes costs about 15 ms on the
colour sensor and 45 ms on the IR sensor, so a loop that alternates modes runs
much slower than one that does not.

## Skills

A skill is a `.py` file in `skills/` defining a function with the same name as
the file. Skills are stored on the laptop, not the brick, so they survive an SD
card reflash. Every skill is sent with every run, so skills can call each other.

The model manages them with `define_skill`, `get_skill`, `list_skills`,
`run_skill` and `delete_skill`, and writes them against the helpers in
`ev3_mcp/brick_api.py`: `drive`, `motor`, `obstacle_cm`, `touch_pressed`,
`color`, `wheel_degrees` and so on.

Brick-side code runs under MicroPython 3.4. No f-strings.

## Room camera

The camera watches the robot from outside, which lets the agent check what the
robot reported against what actually happened.

The agent captures the laptop camera and publishes it to the LiveKit room. Your
phone, the `look` tool and the recorder all read that one track. No browser tab
is needed.

Tools:

- `look(question)` sends the current frame to `qwen/qwen3.8-27b` and returns the
  answer.
- `start_recording()` and `stop_recording()` write MP4 files to `recordings/`.
  Stopping reports the path, duration and frame count.

Capture uses PyAV, which is already installed for encoding. It selects the
camera by name. Laptops with Windows Hello expose a second infrared camera, and
selecting by index can pick that one instead. Set `EV3_CAMERA_DEVICE` if yours
is not called `Integrated Camera`.

There is no setting to enable or disable the camera. Covering the lens is not
the same as turning it off: frames keep arriving, so the agent describes a dark
image rather than reporting no camera.

Recording is local. LiveKit Egress runs on LiveKit's servers and writes to
S3, GCS or Azure, so using it would mean uploading to a bucket and downloading
again.

Implementation notes:

- The first frame after a track starts is low resolution. Measured 384x216
  against 1280x720 once the encoder had ramped up. Wait before capturing.
- Ask `look` narrow questions. On a sparse test image the model added a detail
  that was not there. On a real photo every claim was correct.
- Encoding runs on a worker thread behind a bounded queue. A full queue drops
  frames, because blocking the event loop delays audio.
- Recordings stop at a time limit.

`.venv\Scripts\python.exe scripts/camera_selftest.py` tests capture, publishing,
consumption and recording without the agent or a browser.

## Guardrails

Every run has a time limit, in two layers:

- The brick knows its remaining budget through `time_left()` and
  `out_of_time()`, so a skill can stop early and still print its results.
  Durations are clamped to the remaining budget.
- The host kills the run when the limit passes and forces a stop. Nothing is
  returned, but the robot stops.

Motor state on ev3dev lives in sysfs and outlives the process that set it. Every
program therefore ends with `_safe_stop()`, and the host also forces a stop
after a crash or timeout. Motors do not keep running between tool calls, so
continuous movement has to happen inside one program.

There is no speed limit. Speed is clamped to ±100 because that is the hardware
range. These limits catch mistakes; they are not a security boundary, and the
buttons on the brick are the real stop.

## Running

```
.venv\Scripts\python.exe src/agent.py dev
```

Connect through the [Agents Playground](https://agents-playground.livekit.io)
and talk to it.

Run a skill without the voice layer:

```
.venv\Scripts\python.exe scripts/run_skill.py hardware_check wait_s=40 --timeout 55
```

## Scripts

- `scripts/run_skill.py` runs a saved skill against the robot.
- `scripts/camera_selftest.py` tests the camera path end to end.
- `scripts/camera_test.py` grabs one frame and asks the vision model about it,
  saving the frame so you can see what it was given. It publishes from a browser
  tab, so it also works when the camera is on another machine.
- `scripts/test_mcp_server.py` starts the MCP server over stdio and lists its
  tools.
- `scripts/test_connectivity.py` checks the LiveKit credentials.
- `scripts/test_llm_latency.py` measures time to first token for Groq models.
