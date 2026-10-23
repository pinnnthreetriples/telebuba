// The writing side of an AIDL fast message queue (system/libfmq), for the two queues
// sensorservice hands a sensors HAL in ISensors::initialize.
//
// sensorservice makes both queues and owns their memory; the HAL maps the descriptor's
// grantors (read counter, write counter, ring, event-flag word) and moves the counters.
// The layout is libfmq's (MessageQueueBase.h, android-14.0.0_r75): 64-bit byte counters
// that only grow, a ring of `extent` bytes, a 32-bit futex word woken with
// FUTEX_WAKE_BITSET. Only SynchronizedReadWrite is handled: both sensors queues are.
#pragma once

#include <linux/futex.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <climits>
#include <cstdint>
#include <cstring>
#include <vector>

#include <aidl/android/hardware/common/fmq/MQDescriptor.h>
#include <aidl/android/hardware/common/fmq/SynchronizedReadWrite.h>

namespace hal {

using aidl::android::hardware::common::fmq::MQDescriptor;
using aidl::android::hardware::common::fmq::SynchronizedReadWrite;

class Queue {
  public:
    Queue() = default;
    Queue(const Queue&) = delete;
    Queue& operator=(const Queue&) = delete;
    ~Queue() { unmap(); }

    // Maps `desc`, its counters reset to an empty queue (libfmq's resetPointers).
    template <typename T>
    bool map(const MQDescriptor<T, SynchronizedReadWrite>& desc) {
        unmap();
        if (desc.grantors.size() < 3) return false;
        std::vector<int> fds;
        for (const auto& fd : desc.handle.fds) fds.push_back(fd.get());
        quantum_ = desc.quantum;
        read_ = static_cast<std::atomic<uint64_t>*>(grant(desc, fds, 0));
        write_ = static_cast<std::atomic<uint64_t>*>(grant(desc, fds, 1));
        ring_ = static_cast<uint8_t*>(grant(desc, fds, 2));
        capacity_ = static_cast<uint64_t>(desc.grantors[2].extent);
        if (desc.grantors.size() > 3) flag_ = static_cast<std::atomic<uint32_t>*>(grant(desc, fds, 3));
        if (!read_ || !write_ || !ring_ || capacity_ == 0) {
            unmap();
            return false;
        }
        read_->store(0, std::memory_order_release);
        write_->store(0, std::memory_order_release);
        return true;
    }

    bool mapped() const { return ring_ != nullptr; }
    int32_t quantum() const { return quantum_; }

    // Appends `count` items of `quantum` bytes; false (nothing written) when they don't fit.
    bool write(const void* items, size_t count) {
        if (!mapped()) return false;
        const uint64_t bytes = static_cast<uint64_t>(count) * static_cast<uint64_t>(quantum_);
        const uint64_t head = write_->load(std::memory_order_relaxed);
        const uint64_t tail = read_->load(std::memory_order_acquire);
        if (head - tail > capacity_ || capacity_ - (head - tail) < bytes) return false;
        const uint64_t offset = head % capacity_;
        const uint64_t first = std::min(bytes, capacity_ - offset);
        std::memcpy(ring_ + offset, items, first);
        std::memcpy(ring_, static_cast<const uint8_t*>(items) + first, bytes - first);
        write_->store(head + bytes, std::memory_order_release);
        return true;
    }

    // Consumes everything written so far (the wake-lock queue's counts are not needed).
    void drain() {
        if (mapped()) read_->store(write_->load(std::memory_order_acquire), std::memory_order_release);
    }

    // libfmq's EventFlag::wake: set `bits`, and wake the reader only if one was unset.
    void wake(uint32_t bits) {
        if (!flag_) return;
        const uint32_t old = flag_->fetch_or(bits, std::memory_order_seq_cst);
        if ((~old & bits) != 0) syscall(SYS_futex, flag_, FUTEX_WAKE_BITSET, INT_MAX, nullptr, nullptr, bits);
    }

  private:
    struct Mapping {
        void* base;
        size_t length;
    };

    template <typename T>
    void* grant(const MQDescriptor<T, SynchronizedReadWrite>& desc, const std::vector<int>& fds, size_t index) {
        const auto& g = desc.grantors[index];
        if (g.fdIndex < 0 || static_cast<size_t>(g.fdIndex) >= fds.size() || g.offset < 0 || g.extent <= 0) return nullptr;
        const long page = sysconf(_SC_PAGESIZE);
        const long start = (g.offset / page) * page;
        const size_t length = static_cast<size_t>(g.offset - start + g.extent);
        void* base = mmap(nullptr, length, PROT_READ | PROT_WRITE, MAP_SHARED, fds[g.fdIndex], start);
        if (base == MAP_FAILED) return nullptr;
        mappings_.push_back({base, length});
        return static_cast<uint8_t*>(base) + (g.offset - start);
    }

    void unmap() {
        for (const auto& m : mappings_) munmap(m.base, m.length);
        mappings_.clear();
        read_ = write_ = nullptr;
        ring_ = nullptr;
        flag_ = nullptr;
        capacity_ = 0;
    }

    std::vector<Mapping> mappings_;
    std::atomic<uint64_t>* read_ = nullptr;
    std::atomic<uint64_t>* write_ = nullptr;
    uint8_t* ring_ = nullptr;
    std::atomic<uint32_t>* flag_ = nullptr;
    uint64_t capacity_ = 0;
    int32_t quantum_ = 0;
};

}  // namespace hal
