from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, emit
import serial
import threading
import time
import inputs
import glob
import pickle
import os
import datetime
import sqlite3

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
recording = False
recorded_angles = []
recorded_timestamps = []
record_start_time = None
recording_filename = 'arm_demo.pkl'  # Default recording filename
recording_db_id = None

# --- Gamepad mapping support ---
# Default mapping: code (button/axis) -> (joint index, value function)
gamepad_mapping = {
    'ABS_X': (0, lambda v: int(v / 255 * 180)),      # Axis 0: Base (J1) - Scaled for 0-255 input
    'ABS_Y': (1, lambda v: int(v / 255 * 180)),      # Axis 1: Shoulder (J2) - Scaled for 0-255 input
    'ABS_RZ': (2, lambda v: int(v / 255 * 180)),     # Axis 5: Elbow (J3) - Scaled for 0-255 input
    'BTN_SOUTH': (3, 'toggle'),                     # X/A/Square: Gripper (J4)
}

teleop_mode = 'gamepad'  # 'gamepad' or 'sliders'

DB_PATH = os.path.join(os.path.dirname(__file__), 'datasets.db')
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )''')
        conn.commit()
init_db()

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

@app.route('/static/<path:filename>')
def static_files(filename):
    # Serve static files from the correct directory (templates/ is not standard for static)
    return send_from_directory(os.path.join(app.root_path, 'templates'), filename, as_attachment=False)

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
    global gamepad_connected, latest_gamepad_angles, gamepad_active, teleop_mode, recording, recorded_angles, recorded_timestamps, record_start_time
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
                # --- Recording logic ---
                if recording:
                    now = time.time()
                    t = now - record_start_time if record_start_time else 0.0
                    recorded_angles.append(list(send_angles))
                    recorded_timestamps.append(t)
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

@socketio.on('start_recording')
def start_recording(data=None):
    global recording, recorded_angles, recorded_timestamps, record_start_time, recording_filename, recording_db_id
    recording = True
    recorded_angles = []
    recorded_timestamps = []
    record_start_time = time.time()
    # Always use a temporary name and filename at start
    now = datetime.datetime.now()
    temp_name = f"temp_{now.strftime('%Y%m%d_%H%M%S')}"
    temp_filename = f"{temp_name}_{int(time.time())}.pkl"
    recording_filename = temp_filename
    nowstr = now.strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('INSERT INTO datasets (name, filename, created_at, updated_at) VALUES (?, ?, ?, ?)',
                  (temp_name, temp_filename, nowstr, nowstr))
        global recording_db_id
        recording_db_id = c.lastrowid
        conn.commit()
    print(f'[INFO] Recording started: {recording_filename}')
    emit('ack', {'ok': True})

@socketio.on('stop_recording')
def stop_recording(data=None):
    global recording, recording_filename, recording_db_id
    recording = False
    # Save to LeRobot dataset format
    if recorded_angles and recorded_timestamps:
        # Accept name from data, sanitize, fallback to timestamped default
        name = None
        if data and 'name' in data and data['name'].strip():
            safe = ''.join(c for c in data['name'] if c.isalnum() or c in ('-', '_'))
            if safe:
                name = safe
        if not name:
            now = datetime.datetime.now()
            name = f"dataset_{now.strftime('%Y%m%d_%H%M%S')}"
        filename = f"{name}_{int(time.time())}.pkl"
        # Update DB entry with new name and filename
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('UPDATE datasets SET name=?, filename=?, updated_at=? WHERE id=?',
                      (name, filename, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), recording_db_id))
            conn.commit()
        # Save file with new filename
        with open(filename, 'wb') as f:
            pickle.dump({'angles': recorded_angles, 'timestamps': recorded_timestamps}, f)
        # Remove temp file if it exists
        try:
            if os.path.exists(recording_filename) and recording_filename != filename:
                os.remove(recording_filename)
        except Exception:
            pass
        recording_filename = filename
        print(f'[INFO] Recording stopped. Saved {len(recorded_angles)} steps to {filename}')
    emit('ack', {'ok': True})

@app.route('/list_datasets')
def list_datasets():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, filename, created_at, updated_at FROM datasets ORDER BY updated_at DESC')
        rows = c.fetchall()
    datasets = []
    for row in rows:
        # Only include datasets whose file actually exists
        if os.path.exists(row[2]):
            datasets.append({
                'id': row[0],
                'name': row[1],
                'filename': row[2],
                'date': row[4][:10]  # updated_at date
            })
    return jsonify(datasets)

@app.route('/edit_dataset', methods=['POST'])
def edit_dataset():
    data = request.json
    ds_id = data.get('id')
    new_name = data.get('name')
    if not ds_id or not new_name:
        return jsonify({'ok': False, 'error': 'Missing id or name'})
    safe = ''.join(c for c in new_name if c.isalnum() or c in ('-', '_'))
    if not safe:
        return jsonify({'ok': False, 'error': 'Invalid name'})
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('UPDATE datasets SET name=?, updated_at=? WHERE id=?', (safe, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ds_id))
        conn.commit()
    return jsonify({'ok': True})

@app.route('/delete_dataset', methods=['POST'])
def delete_dataset():
    ds_id = request.args.get('id')
    if not ds_id:
        return jsonify({'ok': False, 'error': 'Missing id'})
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT filename FROM datasets WHERE id=?', (ds_id,))
        row = c.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'})
        filename = row[0]
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})
        c.execute('DELETE FROM datasets WHERE id=?', (ds_id,))
        conn.commit()
    return jsonify({'ok': True})

@app.route('/playback_movement', methods=['POST'])
def playback_movement():
    name = request.args.get('name')
    cycles = int(request.args.get('cycles', 1))
    if not name:
        return jsonify({'ok': False, 'error': 'Missing dataset name'})
    # Find dataset file by name
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT filename FROM datasets WHERE name=? ORDER BY updated_at DESC LIMIT 1', (name,))
        row = c.fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Dataset not found'})
    filename = row[0]
    if not os.path.exists(filename):
        return jsonify({'ok': False, 'error': 'File not found'})
    try:
        with open(filename, 'rb') as f:
            data = pickle.load(f)
        angles = data.get('angles')
        timestamps = data.get('timestamps')
        if not angles or not timestamps:
            return jsonify({'ok': False, 'error': 'No data in dataset'})
        for cycle in range(cycles):
            for i, step in enumerate(angles):
                for j, angle in enumerate(step):
                    if ser and ser.is_open:
                        ser.write(f"J{j+1},{angle}\n".encode())
                # Wait for next timestamp if available
                if i < len(timestamps)-1:
                    dt = timestamps[i+1] - timestamps[i]
                    time.sleep(max(dt, 0.01))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/view_dataset')
def view_dataset():
    name = request.args.get('name')
    if not name:
        return jsonify({'error': 'Missing dataset name'})
    # Find dataset file by name
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT filename FROM datasets WHERE name=? ORDER BY updated_at DESC LIMIT 1', (name,))
        row = c.fetchone()
    if not row:
        return jsonify({'error': 'Dataset not found'})
    filename = row[0]
    if not os.path.exists(filename):
        return jsonify({'error': 'File not found'})
    try:
        with open(filename, 'rb') as f:
            data = pickle.load(f)
        angles = data.get('angles')
        timestamps = data.get('timestamps')
        return jsonify({'angles': angles, 'timestamps': timestamps})
    except Exception as e:
        return jsonify({'error': str(e)})

# Only start background threads if not imported (so only one set runs)
if __name__ == '__main__':
    threading.Thread(target=serial_status_thread, daemon=True).start()
    threading.Thread(target=gamepad_thread, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000)
# --- Patch: Remove gripper from UI mapping wizard text ---
# (This must be done in control.html, not app.py)

@socketio.on('toggle_gripper')
def handle_toggle_gripper(data):
    """
    Toggle the gripper open/closed from the web UI.
    Expects: {open: true/false}
    Sends J4,120 (open) or J4,60 (closed) to the hardware.
    """
    if ser and ser.is_open:
        open_state = data.get('open', False)
        angle = 120 if open_state else 60
        ser.write(f"J4,{angle}\n".encode())
        # Also update latest_gamepad_angles for UI/homing consistency
        if len(latest_gamepad_angles) >= 4:
            latest_gamepad_angles[3] = angle
        print(f"[INFO] Gripper {'opened' if open_state else 'closed'} (J4={angle}) via web UI.")
        emit('ack', {'ok': True})
    else:
        emit('ack', {'ok': False})
