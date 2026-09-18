"""Wait for a button press, read the sensors, then briefly test each motor and verify it turned."""


def hardware_check(wait_s=40):
    import time

    if touch_pressed() is None:
        print("FAIL: no touch sensor attached")
        return

    beep()
    print("armed, waiting up to {}s for the button".format(wait_s))

    deadline = time.time() + wait_s
    pressed = False
    while time.time() < deadline:
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
    # Head is geared and may hit a stop, so give it less than the drive wheels.
    for port, speed, secs in (("A", 15, 0.25), ("B", 20, 0.3), ("C", 20, 0.3)):
        start = motor_position(port)
        if start is None:
            print("{}: EMPTY (no motor on this port)".format(port))
            continue

        motor(port, speed, secs)
        moved = motor_position(port)

        motor(port, -speed, secs)
        settled = motor_position(port)

        delta = moved - start
        residual = settled - start
        verdict = "OK" if abs(delta) > 5 else "NO MOVEMENT"
        print(
            "{}: {} turned {}deg, back to within {}deg".format(
                port, verdict, delta, residual
            )
        )

    stop()
    print("done")
