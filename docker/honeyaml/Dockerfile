FROM rust:latest AS builder
#
# Detect architecture and set target accordingly
RUN arch=$(arch) && \
    if [ "$arch" = "x86_64" ]; then \
        rustup target add x86_64-unknown-linux-musl; \
        echo "TARGET_ARCH=x86_64-unknown-linux-musl" > /target_arch.env; \
    elif [ "$arch" = "aarch64" ]; then \
        rustup target add aarch64-unknown-linux-musl; \
        echo "TARGET_ARCH=aarch64-unknown-linux-musl" > /target_arch.env; \
    else \
        echo "Unsupported architecture: $arch"; \
        exit 1; \
    fi
#
RUN apt update && apt install -y git musl-tools musl-dev
RUN update-ca-certificates
RUN git clone https://github.com/t3chn0m4g3/honeyaml /opt/honeyaml
#
WORKDIR /opt/honeyaml
# Load target architecture from file and build
RUN . /target_arch.env && \
    cargo build --target "$TARGET_ARCH" --release && \
    cp target/"$TARGET_ARCH"/release/honeyaml /opt/honeyaml/
#
FROM alpine:3.20
#
COPY --from=builder /opt/honeyaml/honeyaml /opt/honeyaml/
COPY --from=builder /opt/honeyaml/api.yml /opt/honeyaml/
#
# Setup user, groups and configs
RUN <<EOF
addgroup -g 2000 honeyaml
adduser -S -s /bin/ash -u 2000 -D -g 2000 honeyaml
EOF
#
STOPSIGNAL SIGINT
USER honeyaml:honeyaml
WORKDIR /opt/honeyaml
CMD ["./honeyaml", "-d", "/opt/honeyaml/log"]