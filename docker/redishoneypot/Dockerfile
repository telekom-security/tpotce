# syntax=docker/dockerfile:1
#
# The builder always runs on the build platform and cross-compiles for the
# target, Go under QEMU emulation crashes (SIGSEGV in go mod download).
FROM --platform=$BUILDPLATFORM golang:1.27.2-alpine3.24 AS builder
ARG TARGETOS
ARG TARGETARCH
ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local
#
# Install packages
RUN apk --no-cache -U add git
#
# Fetch RedisHoneyPot v2.0.2 (T-Pot fork of cypwnpwnsocute/RedisHoneyPot)
WORKDIR /root/redishoneypot
RUN git -c init.defaultBranch=main init --quiet && \
    git remote add origin https://github.com/t3chn0m4g3/RedisHoneyPot && \
    git fetch --quiet --depth 1 origin 6eb3446e77e1bb86f269737d8a72572a6dd13e5c && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD
#
# Test on the build platform, then cross-compile for the target
RUN go mod download && \
    go test ./... && \
    GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags="-s -w" -o /root/RedisHoneyPot ./cmd/redishoneypot
#
FROM alpine:3.24
RUN apk --no-cache -U upgrade
#
# Setup redishoneypot
COPY --from=builder /root/RedisHoneyPot /opt/redishoneypot/
#
# Start redishoneypot, the persona comes from REDISHONEYPOT_PROFILE (.env);
# events go to the log file only, stdout keeps start and shutdown
WORKDIR /opt/redishoneypot
USER 2000:2000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD ["./RedisHoneyPot", "healthcheck", "-addr", "127.0.0.1:6379", "-timeout", "2s"]
CMD ["sh", "-c", "exec ./RedisHoneyPot -addr 0.0.0.0:6379 -proto tcp -profile \"${REDISHONEYPOT_PROFILE:-redis74}\" -max-clients 128 -max-command-bytes 1048576 -log-file /var/log/redishoneypot/redishoneypot.log -log-stdout=false"]
