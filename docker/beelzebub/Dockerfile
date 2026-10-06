# syntax=docker/dockerfile:1
#
# The builder always runs on the build platform and cross-compiles for the
# target, Go under QEMU emulation crashes (SIGSEGV in go mod download).
FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS builder
ARG TARGETOS
ARG TARGETARCH
ENV GO111MODULE=on \
    CGO_ENABLED=0
#
# Install packages
RUN apk --no-cache -U add git
#
# Fetch upstream beelzebub v3.9.2
WORKDIR /root/beelzebub
RUN git -c init.defaultBranch=main init --quiet && \
    git remote add origin https://github.com/beelzebub-labs/beelzebub && \
    git fetch --quiet --depth 1 origin 01c5f83946ec7495fbdddf617b25c0aefef44b2d && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD
#
# Add the T-Pot adjustments (flat event log, persistent SSH host key, env
# expansion in service configs), a hook that no longer applies fails the build
COPY dist/tpot/ /root/beelzebub/
COPY dist/tpot.patch /root/tpot.patch
RUN git apply --verbose /root/tpot.patch
#
# Test on the build platform, then cross-compile for the target
COPY dist/configurations/ /root/configurations/
RUN go mod download && \
    mkdir -p /tmp/tpot && \
    BEELZEBUB_TPOT_LOG_FILE=/tmp/tpot/beelzebub.json \
    BEELZEBUB_TPOT_HOST_KEY=/tmp/tpot/ssh_host_key \
    TPOT_SERVICES_DIR=/root/configurations/services \
    LLM_PROVIDER=ollama LLM_MODEL=llama3.1:8b \
    go test ./internal/... && \
    GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags="-s -w" -o main .
#
FROM scratch
#
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /root/beelzebub/main /opt/beelzebub/
COPY dist/configurations /opt/beelzebub/configurations
#
# Start beelzebub
WORKDIR /opt/beelzebub
USER 2000:2000
ENTRYPOINT ["./main", "run"]
