#!/bin/sh
# Generates the AIDL NDK stubs and builds the sensors service for each ABI.
#   build.sh <ndk dir> <aidl binary> <hardware/interfaces> <frameworks/native> <out dir>
set -eu
NDK=$1 AIDL=$2 HI=$3 FN=$4 OUT=$5
HERE=$(cd "$(dirname "$0")" && pwd)
GEN=$(mktemp -d)
SEN=$HI/sensors/aidl/aidl_api/android.hardware.sensors/2
COM=$HI/common/aidl/aidl_api/android.hardware.common/2
FMQ=$HI/common/fmq/aidl/aidl_api/android.hardware.common.fmq/1
# The frozen API versions sensorservice links in Android 14; 13's V1 has the same Event.
for spec in "$COM 2" "$FMQ 1" "$SEN 2"; do
    set -- $spec
    "$AIDL" --lang=ndk --structured --stability=vintf --min_sdk_version=33 \
        --version="$2" --hash="$(tail -n 1 "$1/.hash")" -I "$SEN" -I "$COM" -I "$FMQ" \
        -o "$GEN" -h "$GEN/include" $(find "$1" -name '*.aidl' | sort)
done
CLANG=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
sed 's/#.*//' "$FN/libs/binder/ndk/libbinder_ndk.map.txt" > "$GEN/binder.map"
sed -n 's/^[[:space:]]*\(A[A-Za-z0-9_]*\);.*/void \1(void) {}/p' "$GEN/binder.map" > "$GEN/binder_stub.c"
for abi in arm64-v8a:aarch64 x86_64:x86_64; do
    dir=${abi%%:*} triple=${abi#*:}-linux-android33
    mkdir -p "$OUT/$dir" "$GEN/$dir"
    # Link against a stand-in of the phone's libbinder_ndk.so made from AOSP's own symbol
    # map: the NDK's stub leaves out the LL-NDK calls a HAL service needs (addService,
    # joinThreadPool, markVintfStability). Never shipped; same soname and symbol versions.
    "$CLANG/clang" --target="$triple" -shared -nostdlib -Wl,-soname,libbinder_ndk.so \
        -Wl,--version-script="$GEN/binder.map" -Wl,--undefined-version \
        -o "$GEN/$dir/libbinder_ndk.so" "$GEN/binder_stub.c"
    "$CLANG/clang++" --target="$triple" -std=c++17 -O2 -fPIE -pie -Wall -Werror -Wno-unused-parameter \
        -static-libstdc++ -ffile-prefix-map="$GEN"=gen -ffile-prefix-map="$HERE"=src -ffile-prefix-map="$NDK"=ndk -Wl,--build-id=none -s \
        -I "$GEN/include" -I "$FN/libs/binder/ndk/include_platform" \
        -o "$OUT/$dir/android.hardware.sensors-service.multihal" \
        "$HERE"/src/*.cpp $(find "$GEN" -name '*.cpp' | sort) \
        -L "$GEN/$dir" -lbinder_ndk -llog
done
rm -rf "$GEN"
