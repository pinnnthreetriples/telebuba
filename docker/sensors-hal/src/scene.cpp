#include "scene.h"

#include <algorithm>
#include <cmath>

namespace hal {
namespace {

constexpr double kG = 9.80665;
constexpr double kDeg = M_PI / 180.0;
// Sub-step of the motion: fine enough for the gyro's finite difference at 200 Hz.
constexpr double kStep = 0.005;

Mat rotZ(double a) {
    Mat r;
    r.m[0][0] = std::cos(a), r.m[0][1] = -std::sin(a);
    r.m[1][0] = std::sin(a), r.m[1][1] = std::cos(a);
    return r;
}

Mat rotX(double a) {
    Mat r;
    r.m[1][1] = std::cos(a), r.m[1][2] = -std::sin(a);
    r.m[2][1] = std::sin(a), r.m[2][2] = std::cos(a);
    return r;
}

Mat rotY(double a) {
    Mat r;
    r.m[0][0] = std::cos(a), r.m[0][2] = std::sin(a);
    r.m[2][0] = -std::sin(a), r.m[2][2] = std::cos(a);
    return r;
}

Vec add(const Vec& a, const Vec& b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }

}  // namespace

Vec Mat::apply(const Vec& v) const {
    return {m[0][0] * v.x + m[0][1] * v.y + m[0][2] * v.z, m[1][0] * v.x + m[1][1] * v.y + m[1][2] * v.z,
            m[2][0] * v.x + m[2][1] * v.y + m[2][2] * v.z};
}

Vec Mat::applyT(const Vec& v) const {
    return {m[0][0] * v.x + m[1][0] * v.y + m[2][0] * v.z, m[0][1] * v.x + m[1][1] * v.y + m[2][1] * v.z,
            m[0][2] * v.x + m[1][2] * v.y + m[2][2] * v.z};
}

Mat Mat::operator*(const Mat& o) const {
    Mat r;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) r.m[i][j] = m[i][0] * o.m[0][j] + m[i][1] * o.m[1][j] + m[i][2] * o.m[2][j];
    return r;
}

Quat toQuat(const Mat& r) {
    const auto& m = r.m;
    Quat q;
    const double trace = m[0][0] + m[1][1] + m[2][2];
    if (trace > 0) {
        const double s = std::sqrt(trace + 1.0) * 2;
        q = {(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s};
    } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
        const double s = std::sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2;
        q = {0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s};
    } else if (m[1][1] > m[2][2]) {
        const double s = std::sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2;
        q = {(m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s};
    } else {
        const double s = std::sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2;
        q = {(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s};
    }
    if (q.w < 0) q = {-q.x, -q.y, -q.z, -q.w};  // Android's rotation vector keeps w >= 0
    return q;
}

void Wander::step(double dt, std::mt19937_64& rng) {
    std::normal_distribution<double> normal(0.0, 1.0);
    value += -value / tau * dt + sigma * std::sqrt(2.0 * dt / tau) * normal(rng);
}

Scene::Scene(const SceneConfig& config) : config_(config), rng_(config.seed) {
    const bool hand = config.pose == Pose::Hand;
    // Yaw anywhere; in a hand the top edge tilts 25-50 degrees up toward the face.
    base_[0] = uniform(-M_PI, M_PI);
    base_[1] = hand ? uniform(25, 50) * kDeg : uniform(-0.6, 0.6) * kDeg;
    base_[2] = hand ? uniform(-6, 6) * kDeg : uniform(-0.6, 0.6) * kDeg;
    const double drift[3] = {hand ? 6.0 : 0.02, hand ? 4.0 : 0.01, hand ? 3.0 : 0.01};
    for (int i = 0; i < 3; ++i) {
        target_[i] = {drift[i] * kDeg, uniform(6, 12), 0};
        // Physiological tremor (8-12 Hz) and a slower sway; a desk only hums.
        tremorAmp_[i][0] = (hand ? uniform(0.015, 0.035) : uniform(0.0005, 0.0015)) * kDeg;
        tremorHz_[i][0] = uniform(8, 12);
        tremorAmp_[i][1] = (hand ? uniform(0.08, 0.2) : 0.0) * kDeg;
        tremorHz_[i][1] = uniform(0.3, 1.5);
        for (int k = 0; k < 2; ++k) tremorPhase_[i][k] = uniform(0, 2 * M_PI);
        push_[i] = {hand ? 0.05 : 0.004, 0.4, 0};
        fieldNoise_[i] = {0.25, 30, 0};
    }
    lightLog_ = {hand ? 0.25 : 0.12, 90, 0};
    gameDrift_ = {1.5 * kDeg, 600, 0};
    gameYaw_ = uniform(-M_PI, M_PI);
    accelBias_ = {gauss(0.03), gauss(0.03), gauss(0.05)};
    gyroBias_ = {gauss(0.0008), gauss(0.0008), gauss(0.0008)};
    magBias_ = {gauss(0.8), gauss(0.8), gauss(0.8)};
    headingAccuracy_ = uniform(0.12, 0.45);
    now_ = before_ = orientation(0);
}

double Scene::gauss(double sigma) { return std::normal_distribution<double>(0.0, sigma)(rng_); }

double Scene::uniform(double lo, double hi) { return std::uniform_real_distribution<double>(lo, hi)(rng_); }

Mat Scene::orientation(double t) const {
    double angle[3];
    for (int i = 0; i < 3; ++i) {
        angle[i] = base_[i] + lagged_[i];
        for (int k = 0; k < 2; ++k)
            angle[i] += tremorAmp_[i][k] * std::sin(2 * M_PI * tremorHz_[i][k] * t + tremorPhase_[i][k]);
    }
    return rotZ(angle[0]) * rotX(angle[1]) * rotY(angle[2]);
}

void Scene::stepOnce(double dt) {
    for (int i = 0; i < 3; ++i) {
        target_[i].step(dt, rng_);
        lagged_[i] += (target_[i].value - lagged_[i]) * dt / lag_;
        push_[i].step(dt, rng_);
        fieldNoise_[i].step(dt, rng_);
    }
    lightLog_.step(dt, rng_);
    gameDrift_.step(dt, rng_);
    t_ += dt;
    before_ = now_;
    now_ = orientation(t_);
    dtLast_ = dt;
}

void Scene::advance(int64_t nowNs) {
    if (lastNs_ < 0) {
        lastNs_ = nowNs;
        return;
    }
    // A long idle stretch is not replayed step by step: the processes forget in minutes.
    double left = std::min((nowNs - lastNs_) * 1e-9, 600.0);
    lastNs_ = nowNs;
    while (left > 1e-6) {
        const double dt = std::min(left, kStep);
        stepOnce(dt);
        left -= dt;
    }
}

Vec Scene::gravity() const { return now_.applyT({0, 0, kG}); }

Vec Scene::linear() const { return now_.applyT({push_[0].value, push_[1].value, push_[2].value}); }

Vec Scene::acceleration() const { return add(add(gravity(), linear()), accelBias_); }

Vec Scene::rate() const {
    // before^T * now = I + [w]x dt for the body rate w.
    Mat bt;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) bt.m[i][j] = before_.m[j][i];
    const Mat delta = bt * now_;
    const Vec w{(delta.m[2][1] - delta.m[1][2]) / (2 * dtLast_), (delta.m[0][2] - delta.m[2][0]) / (2 * dtLast_),
                (delta.m[1][0] - delta.m[0][1]) / (2 * dtLast_)};
    return add(w, gyroBias_);
}

Vec Scene::magnetic() const {
    const Vec field = add(config_.field, {fieldNoise_[0].value, fieldNoise_[1].value, fieldNoise_[2].value});
    return add(now_.applyT(field), magBias_);
}

Quat Scene::rotation() const { return toQuat(now_); }

Quat Scene::gameRotation() const { return toQuat(rotZ(gameYaw_ + gameDrift_.value) * now_); }

double Scene::lux() const { return config_.lux * std::exp(lightLog_.value); }

}  // namespace hal
