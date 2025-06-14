from .app import app, socketio, serial_status_thread, gamepad_thread
import threading

# Always start background threads when run as module
threading.Thread(target=serial_status_thread, daemon=True).start()
threading.Thread(target=gamepad_thread, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
