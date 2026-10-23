# The phone's sensors HAL (docs/redroid.md, Sensors): an AIDL android.hardware.sensors
# service built with Google's NDK from AOSP's frozen interface files, for both ABIs.
# Its output is two binaries, not an image:
#
#   docker build --output type=local,dest=emulator/sensors-hal docker/sensors-hal
#
# Every input is pinned: the NDK and the SDK build-tools (whose aidl generates the
# binder stubs) by sha256 from dl.google.com, the AOSP sources by tag and commit from
# android.googlesource.com. The NDK only runs on x86_64, so the build does too; its
# arm64-v8a output is what the arm64 production hosts use.
FROM --platform=linux/amd64 debian:bookworm-slim AS build

ARG NDK_VERSION=r27d
ARG NDK_SHA256=601246087a682d1944e1e16dd85bc6e49560fe8b6d61255be2829178c8ed15d9
# build-tools 34's aidl matches Android 14's binder headers; 36's needs newer ones.
ARG BUILD_TOOLS_ZIP=build-tools_r34-linux.zip
ARG BUILD_TOOLS_SHA256=e858c4b60069d0431051b225d384413b1643e1289b00a4825aed347f25bd510f
ARG AOSP_TAG=android-14.0.0_r75
ARG INTERFACES_COMMIT=5ecadeb354186260fbf2cb223eb712c79b640119
ARG NATIVE_COMMIT=64792e223d982e7032ef17d797484b488bdb887c

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl -fsSL -o ndk.zip "https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip" \
    && echo "${NDK_SHA256}  ndk.zip" | sha256sum -c - \
    && unzip -q ndk.zip && mv "android-ndk-${NDK_VERSION}" ndk && rm ndk.zip \
    && curl -fsSL -o bt.zip "https://dl.google.com/android/repository/${BUILD_TOOLS_ZIP}" \
    && echo "${BUILD_TOOLS_SHA256}  bt.zip" | sha256sum -c - \
    && unzip -q bt.zip && mv android-14 build-tools && rm bt.zip

# Only the directories used, at the tag, refused unless it is the pinned commit.
RUN fetch() { \
        dir=$1 repo=$2 commit=$3; shift 3; \
        git init -q "$dir" && cd "$dir" \
        && git remote add origin "https://android.googlesource.com/$repo" \
        && git sparse-checkout set --no-cone "$@" \
        && git fetch -q --depth 1 --filter=blob:none origin "refs/tags/${AOSP_TAG}" \
        && git checkout -q FETCH_HEAD \
        && test "$(git rev-parse HEAD)" = "$commit" && cd ..; \
    } \
    && fetch interfaces platform/hardware/interfaces "${INTERFACES_COMMIT}" \
        /sensors/aidl/aidl_api/ /common/aidl/aidl_api/ /common/fmq/aidl/aidl_api/ \
    && fetch native platform/frameworks/native "${NATIVE_COMMIT}" \
        /libs/binder/ndk/include_platform/ /libs/binder/ndk/libbinder_ndk.map.txt

COPY build.sh ./hal/
COPY src ./hal/src
RUN sh hal/build.sh /build/ndk /build/build-tools/aidl /build/interfaces /build/native /out

FROM scratch
COPY --from=build /out/ /
