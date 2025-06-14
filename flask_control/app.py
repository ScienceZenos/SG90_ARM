from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import serial
import threading
import time
import inputs
import glob

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')

BAUD_RATE = 115200

# Auto-detect ESP32 serial port
SERIAL_PORT = None
for port in ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2'] + glob.glob('/dev/ttyUSB*'):
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        SERIAL_PORT = port
        serial_connected = True
        print(f"[INFO] Connected to ESP32 on {port}")
        break
    except Exception:
        ser = None
        serial_connected = False
if SERIAL_PORT is None:
    print("[WARN] Could not find ESP32 serial port. Tried /dev/ttyUSB0, /dev/ttyUSB1, /dev/ttyUSB2, ...")

gamepad_connected = False
latest_gamepad_angles = [90, 90, 90, 60]
gamepad_active = False

# --- Gamepad mapping support ---
# Default mapping: code (button/axis) -> (joint index, value function)
gamepad_mapping = {
    'ABS_X': (0, lambda v: int(v / 255 * 180)),      # Axis 0: Base (J1) - Scaled for 0-255 input
    'ABS_Y': (1, lambda v: int(v / 255 * 180)),      # Axis 1: Shoulder (J2) - Scaled for 0-255 input
    'ABS_RZ': (2, lambda v: int(v / 255 * 180)),     # Axis 5: Elbow (J3) - Scaled for 0-255 input
    'BTN_SOUTH': (3, 'toggle'),                     # X/A/Square: Gripper (J4)
}

teleop_mode = 'gamepad'  # 'gamepad' or 'sliders'

# Background thread to emit serial connection status
def serial_status_thread():
    while True:
        socketio.emit('serial_status', {
            'connected': ser is not None and ser.is_open,
            'gamepad': gamepad_connected
        })
        time.sleep(1)

@app.route('/')
def index():
    return render_template('control.html')

@socketio.on('set_teleop_mode')
def handle_set_teleop_mode(data):
    global teleop_mode
    mode = data.get('mode')
    if mode in ['gamepad', 'sliders']:
        teleop_mode = mode
        print(f"[INFO] Teleop mode set to: {teleop_mode}")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})

def gamepad_thread():
    global gamepad_connected, latest_gamepad_angles, gamepad_active, teleop_mode
    gripper_state = 60  # Start closed
    last_x_state = 0
    while True:
        try:
            if teleop_mode != 'gamepad':
                time.sleep(0.1)
                continue
            try:
                gamepads = inputs.devices.gamepads
                if not gamepads:
                    if gamepad_connected:
                        print("[INFO] Gamepad disconnected.")
                        gamepad_connected = False
                    time.sleep(1)
                    continue
                if not gamepad_connected:
                    print(f"[INFO] Gamepad connected: {gamepads[0].name}")
                    gamepad_connected = True
                events = inputs.get_gamepad() # This blocks until an event
            except inputs.UnpluggedError:
                if gamepad_connected:
                    print("[INFO] Gamepad unplugged.")
                    gamepad_connected = False
                time.sleep(1)
                continue
            except Exception as e:
                print(f"[ERROR] Error reading gamepad: {e}")
                if gamepad_connected:
                    gamepad_connected = False
                time.sleep(1)
                continue
            for event in events:
                print(f"[DEBUG] Raw Gamepad Event: code='{event.code}', state={event.state}, ev_type='{event.ev_type}'")
                if event.code == 'BTN_SOUTH':
                    if event.code in gamepad_mapping and gamepad_mapping[event.code][1] == 'toggle':
                        target_joint_idx = gamepad_mapping[event.code][0]
                        if event.state == 1 and last_x_state == 0:
                            current_angle = latest_gamepad_angles[target_joint_idx]
                            new_angle = 120 if current_angle == 60 else 60
                            latest_gamepad_angles[target_joint_idx] = new_angle
                            gamepad_active = True
                            print(f"[DEBUG] BTN_SOUTH pressed. Joint {target_joint_idx} toggled to {new_angle}. Angles: {latest_gamepad_angles}")
                    last_x_state = event.state
                elif event.code in gamepad_mapping:
                    joint_idx, val_fn = gamepad_mapping[event.code]
                    if callable(val_fn):
                        val = val_fn(event.state)
                        # Stick drift filter: ignore small movements near center
                        if event.code in ('ABS_X', 'ABS_Y', 'ABS_RZ'):
                            # For 0-255 range, center is ~127/128. Ignore if within ±10 of center.
                            if abs(event.state - 128) < 10:
                                continue
                        print(f"[DEBUG] Mapped Axis/Button: code='{event.code}', maps to joint_idx={joint_idx}. Raw state={event.state}, calculated_value={val}")
                        if 0 <= joint_idx < len(latest_gamepad_angles):
                            latest_gamepad_angles[joint_idx] = val
                            gamepad_active = True
                        else:
                            print(f"[ERROR] joint_idx {joint_idx} out of range for latest_gamepad_angles (size {len(latest_gamepad_angles)}).")
            if gamepad_active and ser and ser.is_open:
                send_angles = [int(a) for a in latest_gamepad_angles]
                print(f"[DEBUG] Sending to ESP32: J1={send_angles[0]}, J2={send_angles[1]}, J3={send_angles[2]}, J4={send_angles[3]}")
                ser.write(f"J1,{send_angles[0]}\n".encode())
                ser.write(f"J2,{send_angles[1]}\n".encode())
                ser.write(f"J3,{send_angles[2]}\n".encode())
                ser.write(f"J4,{send_angles[3]}\n".encode())
                socketio.emit('current_angles', {'angles': send_angles})
            gamepad_active = False
        except Exception as e:
            print(f"[ERROR] Gamepad thread exception: {e}")
            gamepad_connected = False
            time.sleep(1)

@socketio.on('move_joint')
def handle_move_joint(data):
    global teleop_mode
    if teleop_mode != 'sliders':
        emit('ack', {'ok': False})
        return
    joint = data.get('joint')
    angle = data.get('angle')
    if ser and ser.is_open and joint and angle is not None:
        msg = f"J{joint},{angle}\n"
        ser.write(msg.encode())
        print(f"Sent: {msg.strip()}")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})

@socketio.on('enable_joint')
def handle_enable_joint(data):
    joint = data.get('joint')
    enabled = data.get('enabled')
    if ser and ser.is_open and joint and enabled is not None:
        msg = f"EN,{joint},{enabled}\n"
        ser.write(msg.encode())
        print(f"Sent: {msg.strip()}")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})

@socketio.on('set_home')
def handle_set_home(data):
    joint = data.get('joint')
    pos = data.get('pos')
    if ser and ser.is_open and joint and pos is not None:
        msg = f"HOME,{joint},{pos}\n"
        ser.write(msg.encode())
        print(f"Sent: {msg.strip()}")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})

@socketio.on('go_home')
def handle_go_home():
    if ser and ser.is_open:
        # Soft go home: move each joint gradually to home position
        home_angles = [90, 90, 90, 60]
        current_angles = list(latest_gamepad_angles)
        steps = 30  # Number of steps for smooth movement
        delay = 0.03  # Delay between steps (seconds)
        for step in range(1, steps+1):
            for i in range(4):
                target = home_angles[i]
                start = current_angles[i]
                angle = int(start + (target - start) * step / steps)
                ser.write(f"J{i+1},{angle}\n".encode())
            time.sleep(delay)
        # Update latest_gamepad_angles to home
        for i in range(4):
            latest_gamepad_angles[i] = home_angles[i]
        print("[INFO] Soft go home completed.")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})

@socketio.on('connect')
def handle_connect():
    # Immediately send current serial and gamepad status to the connecting client
    emit('serial_status', {
        'connected': ser is not None and ser.is_open,
        'gamepad': gamepad_connected
    })

# Only start background threads if not imported (so only one set runs)
if __name__ == '__main__':
    threading.Thread(target=serial_status_thread, daemon=True).start()
    threading.Thread(target=gamepad_thread, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000)
# --- Patch: Remove gripper from UI mapping wizard text ---
# (This must be done in control.html, not app.py)
