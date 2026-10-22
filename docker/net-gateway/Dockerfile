# The network side of a mobile account's pod: Android joins this container's network
# namespace, and everything it sends leaves through hev-socks5-tunnel to the account's
# SOCKS5 proxy, or not at all. See docs/redroid.md.
#
#   docker build -t telebuba/net-gateway:2 docker/net-gateway
FROM alpine:3.20

ARG HEV_VERSION=2.18.0
ARG HEV_SHA256_AMD64=1903b23869cbd12b7b57045ca161c0f90314d9c4ef2835e7a9e4a3179bf7d925
ARG HEV_SHA256_ARM64=e44a6965cd718d98829f344e9f300a5664d12020557127b7469bee7c8f3098b0
# Set by BuildKit to the host's architecture: the production hosts are arm64.
ARG TARGETARCH

RUN case "$TARGETARCH" in \
        amd64) asset=x86_64 sha="$HEV_SHA256_AMD64" ;; \
        arm64) asset=arm64 sha="$HEV_SHA256_ARM64" ;; \
        *) echo "no hev-socks5-tunnel build for '$TARGETARCH'" >&2 && exit 1 ;; \
    esac \
    && apk add --no-cache iproute2 iptables ip6tables \
    && wget -q -O /usr/local/bin/hev-socks5-tunnel \
       "https://github.com/heiher/hev-socks5-tunnel/releases/download/${HEV_VERSION}/hev-socks5-tunnel-linux-${asset}" \
    && echo "${sha}  /usr/local/bin/hev-socks5-tunnel" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/hev-socks5-tunnel

COPY --chmod=0755 net-gateway /usr/local/bin/

# Root on purpose: the tunnel opens /dev/net/tun and the kill-switch loads nf_tables
# rules (NET_ADMIN); the container runs nothing else and holds no secrets but the
# proxy file.
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root
USER root
ENTRYPOINT ["/usr/local/bin/net-gateway", "run"]
