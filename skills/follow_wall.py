"""Drive forward, steering away whenever an obstacle gets close. Reports corrections made."""


def follow_wall(seconds=5, target_cm=25, speed=30):
    import time

    end = time.time() + seconds
    corrections = 0
    closest = None

    while time.time() < end:
        ahead = obstacle_cm()
        if ahead is None:
            print("no proximity sensor attached")
            return
        if closest is None or ahead < closest:
            closest = ahead
        if ahead < target_cm:
            drive(speed, -speed)
            corrections += 1
        else:
            drive(speed, speed)
        time.sleep(0.05)

    stop()
    print("corrections: {}  closest: {:.0f}cm".format(corrections, closest))
