# SG90 ARM Control Platform

This project provides a Flask-based web interface and gamepad teleoperation for a 4-DOF SG90 robot arm, with ESP32 firmware and LeRobot compatibility.

## Features
- Real-time control via web UI or HORIPAD mini4 gamepad
- Gamepad mapping hardcoded for reliable operation (no wizard)
- Deadzone filtering to prevent stick drift
- Enable/disable joints, set and go to home positions
- Ready for LeRobot dataset recording (see Phase 4)

## Usage
1. **Start the Flask server:**
   ```bash
   python3 -m flask_control
   ```
2. **Open the web UI:**
   Go to `http://localhost:5000` in your browser.
3. **Connect your HORIPAD mini4 gamepad.**
4. **Enable joints and control the arm!**

## Directory Structure
- `flask_control/` - Flask app, gamepad logic, templates
- `src/main.cpp` - ESP32 firmware for SG90 arm
- `platformio.ini` - PlatformIO config for ESP32

## Gamepad Mapping (HORIPAD mini4)
- Base (J1): Left Stick L/R (Axis 0)
- Shoulder (J2): Left Stick U/D (Axis 1)
- Elbow (J3): Right Stick U/D (Axis 5)
- Gripper (J4): X/A/Square button toggles open/close

## Notes
- The repository is private until submission. Make public when ready to hand in.
- For dataset recording (Phase 4), see the project prompt.

---

© 2025 Ashalen. All rights reserved.
