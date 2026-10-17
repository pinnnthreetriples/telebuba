# The network side of a mobile account's pod: Android joins this container's network
# namespace, and everything it sends leaves through hev-socks5-tunnel to the account's
# SOCKS5 proxy, or not at all. See docs/redroid.md.
#
#   docker build -t telebuba/net-gateway:1 docker/net-gateway
FROM alpine:3.20

ARG HEV_VERSION=2.18.0
ARG HEV_SHA256=1903b23869cbd12b7b57045ca161c0f90314d9c4ef2835e7a9e4a3179bf7d925

RUN apk add --no-cache iproute2 iptables ip6tables \
    && wget -q -O /usr/local/bin/hev-socks5-tunnel \
       "https://github.com/heiher/hev-socks5-tunnel/releases/download/${HEV_VERSION}/hev-socks5-tunnel-linux-x86_64" \
    && echo "${HEV_SHA256}  /usr/local/bin/hev-socks5-tunnel" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/hev-socks5-tunnel

COPY --chmod=0755 net-gateway /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/net-gateway", "run"]
