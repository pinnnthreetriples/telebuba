# The signature-spoofing patch (docs/redroid.md, Signature spoofing): it takes a redroid
# image's /system/framework/services.jar, injects a FakeSig helper and one call in
# ComputerEngine so the framework reports Google's certificate for microG's Play services,
# and outputs the patched services.jar. One per SDK, the image passed as a build context:
#
#   docker build --build-context redroid=docker-image://redroid/redroid:14.0.0_64only-latest \
#     --output type=local,dest=emulator/framework-patch/34 docker/framework-patch
#   docker build --build-context redroid=docker-image://redroid/redroid:13.0.0_64only-latest \
#     --output type=local,dest=emulator/framework-patch/33 docker/framework-patch
#
# ``core.emulator`` copies <dir>/<sdk>/services.jar into a new or refreshed container and
# invalidates its precompiled services.odex so ART runs the patched dex. Needs BuildKit
# (Docker 23+ / `docker buildx`) for --build-context; the services.jar it reads never runs
# here. The tools are pinned by sha256; the certificate is Google's own, public.
FROM debian:bookworm-slim AS build

ARG BAKSMALI_URL=https://github.com/baksmali/smali/releases/download/v2.5.2/baksmali-2.5.2.jar
ARG BAKSMALI_SHA256=d3116248cce4f82ec5a31eb7f95ee75daff42ddf6eed0ab573973dc53fbad2e5
ARG SMALI_URL=https://github.com/baksmali/smali/releases/download/v2.5.2/smali-2.5.2.jar
ARG SMALI_SHA256=9544299578b16f771d8aa8eaefe0d3718ca03478c16f3c356f2fcf1366bfb116

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl openjdk-17-jre-headless python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /w
RUN curl -fsSL -o baksmali.jar "$BAKSMALI_URL" \
    && echo "${BAKSMALI_SHA256}  baksmali.jar" | sha256sum -c - \
    && curl -fsSL -o smali.jar "$SMALI_URL" \
    && echo "${SMALI_SHA256}  smali.jar" | sha256sum -c -

COPY patch.py fakesig.smali.tmpl google_cert.hex /w/
COPY --from=redroid /system/framework/services.jar /w/services.jar
RUN python3 /w/patch.py /w/services.jar /w/out/services.jar

FROM scratch AS out
COPY --from=build /w/out/services.jar /services.jar
