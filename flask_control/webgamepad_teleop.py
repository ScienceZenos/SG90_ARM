# LeRobot-compatible Teleoperator wrapper for Flask/gamepad control
import threading
import time
from .app import app, socketio, latest_gamepad_angles

class Teleoperator:
    def __init__(self):
        pass
    def get_action(self):
        raise NotImplementedError

class WebGamepadTeleop(Teleoperator):
    def __init__(self):
        super().__init__()
        self._flask_thread = threading.Thread(target=self._run_flask, daemon=True)
        self._flask_thread.start()
        # Wait for Flask to be up (optional, for test stability)
        time.sleep(1)
    def _run_flask(self):
        # Only start if not already running
        socketio.run(app, host='0.0.0.0', port=5000)
    def get_action(self):
        # Return latest angles in LeRobot format
        # Angles: [base, shoulder, elbow, gripper]
        # LeRobot expects 4 angles, all 0-180 (gripper: 60/120)
        return {'angles': list(latest_gamepad_angles)}
