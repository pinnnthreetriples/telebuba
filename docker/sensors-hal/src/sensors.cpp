// An AIDL android.hardware.sensors service whose sensors read a simulated phone (scene.h).
//
// The sensor list comes from a tab-separated file (argv[1]) that Telebuba writes for each
// phone: its real chips' names, vendors and ranges, and the phone's seed, pose, light and
// local geomagnetic field. Lines:
//   seed  <u64>
//   pose  hand|desk
//   lux   <base lux>
//   field <east> <north> <up>   (uT)
//   sensor <handle> <type> <flags> <version> <maxRange> <resolution> <power mA>
//          <minDelay us> <maxDelay us> <fifoReserved> <fifoMax> <name> <vendor>
//
// One worker thread produces the events of every active sensor at its sampling period and
// writes them to sensorservice's event queue (fmq.h). Direct channels, data injection and
// dynamic sensors are not offered.
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/log.h>
#include <time.h>

#include <aidl/android/hardware/sensors/BnSensors.h>

#include <chrono>
#include <cmath>
#include <condition_variable>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "fmq.h"
#include "scene.h"

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "SensorsHal", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "SensorsHal", __VA_ARGS__)

namespace hal {
namespace {

using aidl::android::hardware::sensors::BnSensors;
using aidl::android::hardware::sensors::Event;
using aidl::android::hardware::sensors::ISensors;
using aidl::android::hardware::sensors::ISensorsCallback;
using aidl::android::hardware::sensors::SensorInfo;
using aidl::android::hardware::sensors::SensorStatus;
using aidl::android::hardware::sensors::SensorType;
using Payload = Event::EventPayload;
using ndk::ScopedAStatus;

constexpr int64_t kOnChangePollNs = 200'000'000;


int64_t bootNs() {
    timespec ts{};
    clock_gettime(CLOCK_BOOTTIME, &ts);
    return int64_t{ts.tv_sec} * 1'000'000'000 + ts.tv_nsec;
}

bool supported(SensorType type) {
    switch (type) {
        case SensorType::ACCELEROMETER:
        case SensorType::MAGNETIC_FIELD:
        case SensorType::GYROSCOPE:
        case SensorType::LIGHT:
        case SensorType::PROXIMITY:
        case SensorType::GRAVITY:
        case SensorType::LINEAR_ACCELERATION:
        case SensorType::ROTATION_VECTOR:
        case SensorType::GAME_ROTATION_VECTOR:
            return true;
        default:
            return false;
    }
}

bool onChange(const SensorInfo& info) {
    return (info.flags & SensorInfo::SENSOR_FLAG_BITS_MASK_REPORTING_MODE) ==
           SensorInfo::SENSOR_FLAG_BITS_ON_CHANGE_MODE;
}

struct Config {
    SceneConfig scene;
    std::vector<SensorInfo> sensors;
};

// False for a missing file, a line it cannot read, or no sensor it can serve.
bool parse(const std::string& path, Config* config) try {
    std::ifstream in(path);
    if (!in) return false;
    std::string line;
    while (std::getline(in, line)) {
        std::vector<std::string> f;
        std::stringstream fields(line);
        for (std::string part; std::getline(fields, part, '\t');) f.push_back(part);
        if (f.empty() || f[0].empty() || f[0][0] == '#') continue;
        if (f[0] == "seed" && f.size() == 2) {
            config->scene.seed = std::stoull(f[1]);
        } else if (f[0] == "pose" && f.size() == 2) {
            config->scene.pose = f[1] == "desk" ? Pose::Desk : Pose::Hand;
        } else if (f[0] == "lux" && f.size() == 2) {
            config->scene.lux = std::stod(f[1]);
        } else if (f[0] == "field" && f.size() == 4) {
            config->scene.field = {std::stod(f[1]), std::stod(f[2]), std::stod(f[3])};
        } else if (f[0] == "sensor" && f.size() == 14) {
            SensorInfo s;
            s.sensorHandle = std::stoi(f[1]);
            s.type = static_cast<SensorType>(std::stoi(f[2]));
            s.flags = std::stoi(f[3]);
            s.version = std::stoi(f[4]);
            s.maxRange = std::stof(f[5]);
            s.resolution = std::stof(f[6]);
            s.power = std::stof(f[7]);
            s.minDelayUs = std::stoi(f[8]);
            s.maxDelayUs = std::stoi(f[9]);
            s.fifoReservedEventCount = std::stoi(f[10]);
            s.fifoMaxEventCount = std::stoi(f[11]);
            s.name = f[12];
            s.vendor = f[13];
            if (supported(s.type)) config->sensors.push_back(s);
        } else {
            return false;
        }
    }
    return !config->sensors.empty();
} catch (const std::exception&) {
    return false;
}

struct Active {
    bool enabled = false;
    int64_t periodNs = 200'000'000;
    int64_t nextNs = 0;
    bool reported = false;  // on-change: something sent since activation
    float last = 0;
};

class Sensors : public BnSensors {
  public:
    explicit Sensors(Config config) : scene_(config.scene), list_(std::move(config.sensors)) {
        for (const auto& s : list_) state_[s.sensorHandle];
        worker_ = std::thread([this] { run(); });
    }

    ScopedAStatus activate(int32_t handle, bool enabled) override {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = state_.find(handle);
        if (it == state_.end()) return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        Active& a = it->second;
        if (enabled && !a.enabled) {
            a.nextNs = bootNs();
            a.reported = false;
        }
        a.enabled = enabled;
        cv_.notify_all();
        return ScopedAStatus::ok();
    }

    ScopedAStatus batch(int32_t handle, int64_t periodNs, int64_t /*maxReportLatencyNs*/) override {
        std::lock_guard<std::mutex> lock(mutex_);
        const SensorInfo* info = find(handle);
        if (info == nullptr) return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        int64_t lo = int64_t{info->minDelayUs} * 1000;
        int64_t hi = int64_t{info->maxDelayUs} * 1000;
        if (onChange(*info)) lo = std::max<int64_t>(lo, kOnChangePollNs);
        if (hi <= 0) hi = 1'000'000'000;
        state_[handle].periodNs = std::clamp(periodNs, lo > 0 ? lo : 1'000'000, std::max(hi, lo));
        cv_.notify_all();
        return ScopedAStatus::ok();
    }

    ScopedAStatus configDirectReport(int32_t, int32_t, ISensors::RateLevel, int32_t*) override {
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }

    ScopedAStatus flush(int32_t handle) override {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = state_.find(handle);
        if (it == state_.end() || !it->second.enabled) return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        Event e;
        e.timestamp = 0;
        e.sensorHandle = handle;
        e.sensorType = SensorType::META_DATA;
        Payload::MetaData meta;
        meta.what = Payload::MetaData::MetaDataEventType::META_DATA_FLUSH_COMPLETE;
        e.payload.set<Payload::meta>(meta);
        send({e});
        return ScopedAStatus::ok();
    }

    ScopedAStatus getSensorsList(std::vector<SensorInfo>* out) override {
        *out = list_;
        return ScopedAStatus::ok();
    }

    ScopedAStatus initialize(const MQDescriptor<Event, SynchronizedReadWrite>& events,
                             const MQDescriptor<int32_t, SynchronizedReadWrite>& wakeLocks,
                             const std::shared_ptr<ISensorsCallback>& callback) override {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& [handle, a] : state_) a.enabled = false;
        callback_ = callback;
        if (!events_.map(events) || !wakeLocks_.map(wakeLocks)) {
            LOGE("cannot map the event queues");
            return ScopedAStatus::fromServiceSpecificError(ISensors::ERROR_BAD_VALUE);
        }
        if (events_.quantum() != static_cast<int32_t>(sizeof(Event))) {
            LOGE("event size %d, built for %zu", events_.quantum(), sizeof(Event));
            return ScopedAStatus::fromServiceSpecificError(ISensors::ERROR_BAD_VALUE);
        }
        LOGI("initialized with %zu sensors", list_.size());
        return ScopedAStatus::ok();
    }

    ScopedAStatus injectSensorData(const Event&) override {
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }

    ScopedAStatus registerDirectChannel(const ISensors::SharedMemInfo&, int32_t*) override {
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }

    ScopedAStatus setOperationMode(ISensors::OperationMode mode) override {
        if (mode == ISensors::OperationMode::NORMAL) return ScopedAStatus::ok();
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }

    ScopedAStatus unregisterDirectChannel(int32_t) override {
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }

  private:
    const SensorInfo* find(int32_t handle) const {
        for (const auto& s : list_)
            if (s.sensorHandle == handle) return &s;
        return nullptr;
    }

    // Caller holds mutex_.
    void send(const std::vector<Event>& events) {
        if (events.empty() || !events_.mapped()) return;
        if (!events_.write(events.data(), events.size())) {
            LOGE("event queue full, %zu events dropped", events.size());
            return;
        }
        events_.wake(ISensors::EVENT_QUEUE_FLAG_BITS_READ_AND_PROCESS);
    }

    float quantize(float value, float resolution) {
        return resolution > 0 ? std::round(value / resolution) * resolution : value;
    }

    Payload::Vec3 vec3(const Vec& v, const SensorInfo& info, double noise) {
        Payload::Vec3 out;
        out.x = quantize(static_cast<float>(v.x + scene_.gauss(noise)), info.resolution);
        out.y = quantize(static_cast<float>(v.y + scene_.gauss(noise)), info.resolution);
        out.z = quantize(static_cast<float>(v.z + scene_.gauss(noise)), info.resolution);
        out.status = SensorStatus::ACCURACY_HIGH;
        return out;
    }

    Quat jitter(Quat q, double noise) {
        q = {q.x + scene_.gauss(noise), q.y + scene_.gauss(noise), q.z + scene_.gauss(noise), q.w};
        const double n = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
        return {q.x / n, q.y / n, q.z / n, q.w / n};
    }

    // One reading of `info` at `t`, or false for an on-change sensor that has not changed.
    bool sample(const SensorInfo& info, Active& a, int64_t t, Event* e) {
        e->timestamp = t;
        e->sensorHandle = info.sensorHandle;
        e->sensorType = info.type;
        switch (info.type) {
            case SensorType::ACCELEROMETER:
                e->payload.set<Payload::vec3>(vec3(scene_.acceleration(), info, 0.012));
                return true;
            case SensorType::MAGNETIC_FIELD:
                e->payload.set<Payload::vec3>(vec3(scene_.magnetic(), info, 0.3));
                return true;
            case SensorType::GYROSCOPE:
                e->payload.set<Payload::vec3>(vec3(scene_.rate(), info, 0.0012));
                return true;
            case SensorType::GRAVITY:
                e->payload.set<Payload::vec3>(vec3(scene_.gravity(), info, 0.002));
                return true;
            case SensorType::LINEAR_ACCELERATION:
                e->payload.set<Payload::vec3>(vec3(scene_.linear(), info, 0.008));
                return true;
            case SensorType::ROTATION_VECTOR: {
                const Quat q = jitter(scene_.rotation(), 0.0004);
                Payload::Data d;
                d.values[0] = q.x, d.values[1] = q.y, d.values[2] = q.z, d.values[3] = q.w;
                d.values[4] = static_cast<float>(scene_.headingAccuracy());
                e->payload.set<Payload::data>(d);
                return true;
            }
            case SensorType::GAME_ROTATION_VECTOR: {
                const Quat q = jitter(scene_.gameRotation(), 0.0003);
                Payload::Vec4 v;
                v.x = q.x, v.y = q.y, v.z = q.z, v.w = q.w;
                e->payload.set<Payload::vec4>(v);
                return true;
            }
            case SensorType::LIGHT:
            case SensorType::PROXIMITY: {
                const float value = info.type == SensorType::PROXIMITY
                                            ? info.maxRange
                                            : quantize(static_cast<float>(scene_.lux() * (1 + scene_.gauss(0.01))),
                                                       std::max(info.resolution, 1.0f));
                if (a.reported && value == a.last) return false;
                a.reported = true;
                a.last = value;
                e->payload.set<Payload::scalar>(value);
                return true;
            }
            default:
                return false;
        }
    }

    void run() {
        std::unique_lock<std::mutex> lock(mutex_);
        for (;;) {
            int64_t next = INT64_MAX;
            for (const auto& [handle, a] : state_)
                if (a.enabled) next = std::min(next, a.nextNs);
            if (next == INT64_MAX) {
                cv_.wait(lock);
                continue;
            }
            const int64_t now = bootNs();
            if (next > now) {
                cv_.wait_for(lock, std::chrono::nanoseconds(next - now));
                continue;
            }
            wakeLocks_.drain();
            scene_.advance(now);
            std::vector<Event> out;
            for (const auto& info : list_) {
                Active& a = state_[info.sensorHandle];
                if (!a.enabled || a.nextNs > now) continue;
                // Real parts tick on their own crystal: a little jitter, never a backlog.
                const int64_t jitter = static_cast<int64_t>(scene_.gauss(a.periodNs * 0.002));
                Event e;
                if (sample(info, a, std::min(now, a.nextNs + jitter), &e)) out.push_back(e);
                a.nextNs += a.periodNs;
                if (a.nextNs <= now) a.nextNs = now + a.periodNs;
            }
            send(out);
        }
    }

    std::mutex mutex_;
    std::condition_variable cv_;
    Scene scene_;
    const std::vector<SensorInfo> list_;
    std::map<int32_t, Active> state_;
    Queue events_;
    Queue wakeLocks_;
    std::shared_ptr<ISensorsCallback> callback_;
    std::thread worker_;
};

}  // namespace
}  // namespace hal

int main(int argc, char** argv) {
    const std::string path = argc > 1 ? argv[1] : "/vendor/etc/sensors/sensors.conf";
    hal::Config config;
    if (!hal::parse(path, &config)) {
        LOGE("no sensors in %s", path.c_str());
        return 1;
    }
    ABinderProcess_setThreadPoolMaxThreadCount(0);
    auto service = ndk::SharedRefBase::make<hal::Sensors>(std::move(config));
    const std::string instance = std::string(hal::ISensors::descriptor) + "/default";
    if (AServiceManager_addService(service->asBinder().get(), instance.c_str()) != STATUS_OK) {
        LOGE("cannot register %s", instance.c_str());
        return 1;
    }
    ABinderProcess_joinThreadPool();
    return 1;
}
