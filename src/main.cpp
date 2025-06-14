#include <Arduino.h>

// Pinout for 4DOF MeArm (update as needed for your hardware):
// Base:     GPIO 13
// Shoulder: GPIO 12
// Elbow:    GPIO 14
// Gripper:  GPIO 27

const int basePin = 13;
const int shoulderPin = 12;
const int elbowPin = 14;
const int gripperPin = 27;

const int freq = 50; // 50 Hz for servos
const int resolution = 16; // 16 bits for fine control

// LEDC channels (must be unique per servo)
const int baseChannel = 0;
const int shoulderChannel = 1;
const int elbowChannel = 2;
const int gripperChannel = 3;

// Pulse width range for SG90: 500us (0°) to 2400us (180°)
const int minUs = 500;
const int maxUs = 2400;
// Gripper safe range (example: closed=60, open=120)
const int gripperClosed = 60;
const int gripperOpen = 120;

// Home positions
int basePos = 90;
int shoulderPos = 90;
int elbowPos = 90;
int gripperPos = gripperClosed;

// Enable flags
bool baseEnabled = false;
bool shoulderEnabled = false;
bool elbowEnabled = false;
bool gripperEnabled = false;

void setServoAngle(int channel, int angle, int minAngle = 0, int maxAngle = 180) {
  angle = constrain(angle, minAngle, maxAngle);
  int us = map(angle, 0, 180, minUs, maxUs);
  uint32_t duty = (uint32_t)((us / 20000.0) * ((1 << resolution) - 1));
  ledcWrite(channel, duty);
}

void setServoEnabled(int joint, bool enabled) {
  switch (joint) {
    case 1: baseEnabled = enabled; break;
    case 2: shoulderEnabled = enabled; break;
    case 3: elbowEnabled = enabled; break;
    case 4: gripperEnabled = enabled; break;
  }
}

void setServoHome(int joint, int pos) {
  switch (joint) {
    case 1: basePos = constrain(pos, 0, 180); break;
    case 2: shoulderPos = constrain(pos, 0, 180); break;
    case 3: elbowPos = constrain(pos, 0, 180); break;
    case 4: gripperPos = constrain(pos, gripperClosed, gripperOpen); break;
  }
}

void goHome() {
  if (baseEnabled) setServoAngle(baseChannel, basePos);
  if (shoulderEnabled) setServoAngle(shoulderChannel, shoulderPos);
  if (elbowEnabled) setServoAngle(elbowChannel, elbowPos);
  if (gripperEnabled) setServoAngle(gripperChannel, gripperPos, gripperClosed, gripperOpen);
}

void setup() {
  Serial.begin(115200);
  // Setup PWM for each servo
  ledcSetup(baseChannel, freq, resolution);
  ledcAttachPin(basePin, baseChannel);
  ledcSetup(shoulderChannel, freq, resolution);
  ledcAttachPin(shoulderPin, shoulderChannel);
  ledcSetup(elbowChannel, freq, resolution);
  ledcAttachPin(elbowPin, elbowChannel);
  ledcSetup(gripperChannel, freq, resolution);
  ledcAttachPin(gripperPin, gripperChannel);
  // Set home positions
  setServoAngle(baseChannel, basePos);
  setServoAngle(shoulderChannel, shoulderPos);
  setServoAngle(elbowChannel, elbowPos);
  setServoAngle(gripperChannel, gripperPos, gripperClosed, gripperOpen);
}

// --- Advanced Smoothing: EMA + Velocity + Acceleration Limiting (S-curve) + PID ---
float baseTarget = 90, shoulderTarget = 90, elbowTarget = 90, gripperTarget = gripperClosed;
float baseFiltered = 90, shoulderFiltered = 90, elbowFiltered = 90, gripperFiltered = gripperClosed;
float baseVel = 0, shoulderVel = 0, elbowVel = 0, gripperVel = 0;
float baseErrSum = 0, shoulderErrSum = 0, elbowErrSum = 0, gripperErrSum = 0;
float baseLastErr = 0, shoulderLastErr = 0, elbowLastErr = 0, gripperLastErr = 0;
int lastBasePwm = -1, lastShoulderPwm = -1, lastElbowPwm = -1, lastGripperPwm = -1;
const float EMA_ALPHA = 0.08; // Smoothing
const int PWM_UPDATE_THRESHOLD = 0; // Update on any change
unsigned long lastServoUpdate = 0;
const unsigned long SERVO_UPDATE_INTERVAL = 10; // ms (faster update for smoother PID)
const float MAX_DEG_PER_UPDATE = 2.2; // Faster, but still smooth
const float MAX_DEG_ACCEL = 0.28;     // Faster acceleration, still controlled
// PID parameters (tuned for all SG90 joints, minimal overshoot, no self-movement)
const float KP = 0.10;   // Lower P for gentle correction
const float KI = 0.001;  // Very low I to avoid windup and drift
const float KD = 0.14;   // Higher D for strong damping;

void updateJointPID(float &target, float &filtered, float &vel, float &errSum, float &lastErr, int &lastPwm, int channel, int minA, int maxA, bool enabled) {
  if (!enabled) return;
  float ema = EMA_ALPHA * target + (1.0 - EMA_ALPHA) * filtered;
  float error = ema - filtered;
  errSum += error;
  // Anti-windup: clamp integral
  errSum = constrain(errSum, -30, 30);
  float dErr = error - lastErr;
  lastErr = error;
  // PID output
  float pid = KP * error + KI * errSum + KD * dErr;
  // S-curve velocity/accel limiting
  float desiredVel = pid;
  if (fabs(desiredVel) > MAX_DEG_PER_UPDATE) desiredVel = (desiredVel > 0 ? 1 : -1) * MAX_DEG_PER_UPDATE;
  float accel = desiredVel - vel;
  if (fabs(accel) > MAX_DEG_ACCEL) accel = (accel > 0 ? 1 : -1) * MAX_DEG_ACCEL;
  vel += accel;
  filtered += vel;
  int pwmVal = round(constrain(filtered, minA, maxA));
  if (abs(pwmVal - lastPwm) > PWM_UPDATE_THRESHOLD) {
    if (channel == gripperChannel) {
      setServoAngle(channel, pwmVal, gripperClosed, gripperOpen);
    } else {
      setServoAngle(channel, pwmVal);
    }
    lastPwm = pwmVal;
  }
}

void loop() {
  // --- 1. Parse serial and update target angles ---
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("J1,")) {
      int val = cmd.substring(3).toInt();
      if (baseEnabled) baseTarget = 180 - constrain(val, 0, 180); // Invert base direction
    } else if (cmd.startsWith("J2,")) {
      int val = cmd.substring(3).toInt();
      if (shoulderEnabled) shoulderTarget = constrain(val, 0, 180);
    } else if (cmd.startsWith("J3,")) {
      int val = cmd.substring(3).toInt();
      if (elbowEnabled) elbowTarget = constrain(val, 0, 180);
    } else if (cmd.startsWith("J4,")) {
      int val = cmd.substring(3).toInt();
      if (gripperEnabled) gripperTarget = constrain(val, gripperClosed, gripperOpen);
    } else if (cmd.startsWith("EN,")) {
      int joint = cmd.substring(3,4).toInt();
      int en = cmd.substring(5).toInt();
      setServoEnabled(joint, en == 1);
    } else if (cmd.startsWith("HOME,")) {
      int joint = cmd.substring(5,6).toInt();
      int pos = cmd.substring(7).toInt();
      setServoHome(joint, pos);
    } else if (cmd == "GOHOME") {
      goHome();
    }
  }

  // --- 2. Every 10ms, update filtered angles and PWM ---
  unsigned long now = millis();
  if (now - lastServoUpdate >= SERVO_UPDATE_INTERVAL) {
    lastServoUpdate = now;
    updateJointPID(baseTarget, baseFiltered, baseVel, baseErrSum, baseLastErr, lastBasePwm, baseChannel, 0, 180, baseEnabled);
    updateJointPID(shoulderTarget, shoulderFiltered, shoulderVel, shoulderErrSum, shoulderLastErr, lastShoulderPwm, shoulderChannel, 0, 180, shoulderEnabled);
    updateJointPID(elbowTarget, elbowFiltered, elbowVel, elbowErrSum, elbowLastErr, lastElbowPwm, elbowChannel, 0, 180, elbowEnabled);
    updateJointPID(gripperTarget, gripperFiltered, gripperVel, gripperErrSum, gripperLastErr, lastGripperPwm, gripperChannel, gripperClosed, gripperOpen, gripperEnabled);
  }
}