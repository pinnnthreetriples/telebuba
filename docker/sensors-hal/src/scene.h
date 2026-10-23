// The phone's motion and surroundings, from which every sensor reads.
//
// World frame: East-North-Up, as Android's rotation vector. Device frame: x right, y up
// the screen, z out of it. The phone is held in a hand (tilted toward the face, drifting
// slowly, with a physiological tremor) or lies on a desk; its orientation wanders
// smoothly, so the gyroscope is the derivative of what the accelerometer and the
// magnetometer see. Each phone's biases, base pose and light come from its seed.
#pragma once

#include <cstdint>
#include <random>

namespace hal {

struct Vec {
    double x = 0, y = 0, z = 0;
};

struct Quat {
    double x = 0, y = 0, z = 0, w = 1;
};

struct Mat {
    double m[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    Vec apply(const Vec& v) const;
    Vec applyT(const Vec& v) const;  // the transpose: world -> device
    Mat operator*(const Mat& o) const;
};

enum class Pose { Hand, Desk };

struct SceneConfig {
    uint64_t seed = 1;
    Vec field{0.0, 20.0, -40.0};  // geomagnetic field, ENU, uT
    Pose pose = Pose::Hand;
    double lux = 150.0;
};

// An Ornstein-Uhlenbeck process: mean 0, stationary deviation `sigma`, time constant `tau`.
struct Wander {
    double sigma = 0, tau = 1, value = 0;
    void step(double dt, std::mt19937_64& rng);
};

class Scene {
  public:
    explicit Scene(const SceneConfig& config);

    void advance(int64_t nowNs);

    Vec gravity() const;        // device frame, m/s^2
    Vec linear() const;         // device frame, m/s^2
    Vec acceleration() const;   // gravity + linear, plus the accelerometer's bias
    Vec rate() const;           // angular rate, device frame, rad/s, plus the gyro's bias
    Vec magnetic() const;       // device frame, uT, plus the residual hard-iron offset
    Quat rotation() const;      // device -> ENU
    Quat gameRotation() const;  // device -> a frame whose heading is arbitrary and drifts
    double headingAccuracy() const { return headingAccuracy_; }
    double lux() const;

    double gauss(double sigma);
    double uniform(double lo, double hi);

  private:
    Mat orientation(double t) const;
    void stepOnce(double dt);

    SceneConfig config_;
    std::mt19937_64 rng_;
    double t_ = 0;  // seconds since the first advance
    int64_t lastNs_ = -1;

    // Yaw, pitch, roll (rad): base + a lagged wander + tremor.
    double base_[3] = {0, 0, 0};
    Wander target_[3];
    double lagged_[3] = {0, 0, 0};
    double lag_ = 0.6;
    double tremorAmp_[3][2] = {};
    double tremorHz_[3][2] = {};
    double tremorPhase_[3][2] = {};

    Wander push_[3];      // the hand's own acceleration, ENU
    Wander lightLog_;     // log-lux around the base
    Wander fieldNoise_[3];
    Wander gameDrift_;
    double gameYaw_ = 0;

    Mat now_, before_;
    double dtLast_ = 0.005;
    Vec accelBias_, gyroBias_, magBias_;
    double headingAccuracy_ = 0.3;
};

Quat toQuat(const Mat& r);

}  // namespace hal
