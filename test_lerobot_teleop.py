import time
from lerobot import load_teleoperator

teleop = load_teleoperator("WebGamepadTeleop")
print("Loaded WebGamepadTeleop via LeRobot. Move the sliders or gamepad in the web UI.")

for i in range(20):
    print(f"Step {i+1}: {teleop.get_action()}")
    time.sleep(1)
