'Follow a wall using the ultrasonic sensor.'


def follow_wall(seconds=5, target_cm=15, speed=30):
    import time
    end = time.time() + seconds
    hits = 0
    while time.time() < end:
        d = distance_cm()
        if d is None:
            print("no ultrasonic sensor")
            return
        if d < target_cm:
            drive(speed, -speed)
            hits += 1
        else:
            drive(speed, speed)
        time.sleep(0.05)
    stop()
    print("corrections: {}".format(hits))
