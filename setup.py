from setuptools import setup, find_packages

setup(
    name="flask_control",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "flask",
        "flask-socketio",
    ],
    entry_points={
        "lerobot.teleoperator": [
            "WebGamepadTeleop = flask_control.webgamepad_teleop:WebGamepadTeleop"
        ]
    },
)
