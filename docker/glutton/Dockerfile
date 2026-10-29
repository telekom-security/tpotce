FROM golang:1.26-trixie AS builder

ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl g++ git libpcap-dev libssl-dev make zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# Zeek's Spicy 1.17 toolchain is available for both T-Pot architectures.
RUN case "${TARGETARCH}" in \
      amd64) mySHA=df40ac3cb0e9cb845bf198a48ac1cc2cf4f8961686aeaee839f45ffb6884bca6 ;; \
      arm64) mySHA=950311084cdc013ece0a5126dfd8538c2104e3dfe663b7d2c45a450700c43cad ;; \
      *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL --retry 3 -o /tmp/spicy.deb \
      "https://download.opensuse.org/repositories/security:/zeek/Debian_13/${TARGETARCH}/zeek-9.0-spicy-dev_9.0.1-0_${TARGETARCH}.deb" && \
    echo "${mySHA}  /tmp/spicy.deb" | sha256sum -c - && \
    dpkg -i /tmp/spicy.deb && \
    rm /tmp/spicy.deb && \
    ln -s /opt/zeek /opt/spicy && \
    /opt/spicy/bin/spicyc --version | grep -Fx 'spicyc v1.17.0'

WORKDIR /opt/glutton
RUN git init && \
    git remote add origin https://github.com/mushorg/glutton && \
    git fetch --depth 1 origin e1efb9c39a9d036262b52221104e32aae12df3fb && \
    git checkout --detach FETCH_HEAD && \
    go mod download
COPY dist/tpot.patch /tmp/tpot.patch
RUN git apply --check /tmp/tpot.patch && git apply /tmp/tpot.patch
COPY dist/tpot/ ./
RUN mv tpot_events.go tpot_events_test.go producer/
ENV PATH="/opt/spicy/bin:${PATH}"
RUN make spicy CXX=g++ && \
    CGO_CXXFLAGS="$(spicy-config --cxxflags)" go test -v -timeout 30s -run '^TestTpot' . ./producer && \
    CGO_CXXFLAGS="$(spicy-config --cxxflags)" \
    go build -trimpath -ldflags="-X main.VERSION=glutton-e1efb9c-tpot" -o bin/server app/server.go
COPY dist/config.yaml config/config.yaml

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates iproute2 libcap2-bin libpcap0.8t64 libstdc++6 nftables zlib1g && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /var/log/glutton /opt/glutton/payloads /opt/glutton/samples /var/lib/glutton
COPY --from=builder /opt/glutton/bin/server /opt/glutton/bin/server
COPY --from=builder /opt/glutton/config /opt/glutton/config
COPY --chmod=0755 dist/capture-if.sh /usr/bin/capture-if.sh
COPY --chmod=0755 dist/entrypoint.sh /opt/glutton/entrypoint.sh
RUN setcap cap_net_admin,cap_net_raw=ep /opt/glutton/bin/server && \
    setcap cap_net_admin=ep /usr/sbin/nft
WORKDIR /opt/glutton
USER 2000:2000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/glutton/entrypoint.sh"]
