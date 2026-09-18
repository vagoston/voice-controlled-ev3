"""Drive a known distance and turn a known angle, reporting what the encoders saw.

Checks the measured geometry against reality. Encoder error means the profile
numbers are wrong; wheel slip means they are right but the robot did not go
where the encoders think it did, which only a tape measure or the room camera
can tell you.
"""


def calibrate_movement(cm=20, degrees=360, speed=30):
    print("wheel: {:.3f}mm per degree".format(mm_per_wheel_degree()))
    print("turn:  {:.2f} wheel degrees per robot degree".format(
        wheel_degrees_per_robot_degree()))

    print("--- straight line ---")
    print("mark where the robot is, then measure after it stops")
    reset_wheels()
    actual = drive_cm(cm, speed)
    print("asked {}cm, encoders say {:.1f}cm, error {:+.1f}cm".format(
        cm, actual, actual - cm))
    print("wheel positions after: {}".format(wheel_degrees()))

    if out_of_time():
        print("out of budget before the turn")
        return

    sleep(1)

    print("--- turn on the spot ---")
    print("note which way it faces first: after {} degrees it should match".format(degrees))
    reset_wheels()
    turned = turn(degrees, speed)
    print("asked {} degrees, encoders say {:.1f}, error {:+.1f}".format(
        degrees, turned, turned - degrees))
    print("wheel positions after: {}".format(wheel_degrees()))

    print("--- what the numbers mean ---")
    print("encoder error near zero means the motors did as told")
    print("if it did not physically return to its heading, the wheels slipped")
    print("or axle_track_mm is wrong")
