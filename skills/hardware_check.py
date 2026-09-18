"""Wait for a button press, read the sensors, then briefly test each motor and verify it turned."""


def hardware_check(wait_s=40):
    import time

    if touch_pressed() is None:
        print("FAIL: no touch sensor attached")
        return

    # Leave enough budget for the motor tests, so overrunning the wait still
    # produces a report rather than being killed silently.
    wait_s = min(wait_s, max(0.0, time_left() - 6.0))

    beep()
    print("armed, waiting up to {:.0f}s for the button".format(wait_s))

    deadline = time.time() + wait_s
    pressed = False
    while time.time() < deadline and not out_of_time():
        if touch_pressed():
            pressed = True
            break
        time.sleep(0.05)

    if not pressed:
        print("TIMEOUT: button never pressed")
        return

    beep()
    while touch_pressed():
        time.sleep(0.05)

    print("--- sensors ---")
    print("proximity:    {}".format(proximity()))
    print("obstacle_cm:  {}".format(obstacle_cm()))
    print("colour:       {}".format(color()))
    print("reflected:    {}".format(reflected_light()))
    print("touch:        {}".format(touch_pressed()))

    print("--- motors ---")
    for role in motor_roles():
        # Non-drive motors are geared and may hit a stop, so they get less.
        drive_motor = role in ("left", "right")
        speed = 20 if drive_motor else 15
        secs = 0.3 if drive_motor else 0.25

        start = motor_position(role)
        if start is None:
            print("{}: EMPTY (no motor on port {})".format(role, resolve_port(role)))
            continue

        motor(role, speed, secs)
        moved = motor_position(role)

        motor(role, -speed, secs)
        settled = motor_position(role)

        delta = moved - start
        residual = settled - start
        verdict = "OK" if abs(delta) > 5 else "NO MOVEMENT"
        print(
            "{} (port {}): {} turned {}deg, back to within {}deg".format(
                role, resolve_port(role), verdict, delta, residual
            )
        )

    stop()
    print("done")
