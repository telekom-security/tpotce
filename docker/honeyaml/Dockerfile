FROM rust:latest AS builder
#
RUN apt update && apt install -y git musl-tools musl-dev libzstd-dev
RUN update-ca-certificates
RUN git clone https://github.com/t3chn0m4g3/honeyaml /opt/honeyaml
#
WORKDIR /opt/honeyaml
# Need to dynamically link, otherwise multi platform builds are breaking with zstd-sys
RUN cargo build --release && \
    cp target/release/honeyaml /opt/honeyaml/
#
# Using wolfi instead of ubuntu because of smaller footprint (and required full glibc support)
FROM chainguard/wolfi-base:latest
#
COPY --from=builder /opt/honeyaml/honeyaml /opt/honeyaml/
COPY --from=builder /opt/honeyaml/api.yml /opt/honeyaml/
#
# Setup user, groups and configs
RUN apk -U --no-cache add libstdc++
#
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/honeyaml
CMD ["./honeyaml", "-d", "/opt/honeyaml/log"]