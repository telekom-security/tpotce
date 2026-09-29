# syntax=docker/dockerfile:1
#
# The builder always runs on the build platform and cross-compiles for the
# target, Go under QEMU emulation crashes. Galah needs CGO (sqlite), xx
# provides the cross toolchain.
FROM --platform=$BUILDPLATFORM tonistiigi/xx:1.9.0 AS xx
#
FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS builder
COPY --from=xx / /
ARG TARGETPLATFORM
ENV CGO_ENABLED=1
#
# Install packages, native ones for the tests and target ones for the build
RUN apk --no-cache -U add clang lld git gcc musl-dev && \
    xx-apk --no-cache add gcc musl-dev
#
# Fetch upstream galah v1.1.1
WORKDIR /opt/galah
RUN git -c init.defaultBranch=main init --quiet && \
    git remote add origin https://github.com/0x4D31/galah && \
    git fetch --quiet --depth 1 origin 8f77b85e892731cf1402963d8121c79fef0e59c7 && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD
#
# Add the T-Pot adjustment (flat event log), a hook that no longer applies
# fails the build
COPY dist/tpot/ /opt/galah/
COPY dist/tpot.patch /root/tpot.patch
RUN git apply --verbose /root/tpot.patch
#
# Test on the build platform, then cross-compile for the target (the binary
# goes outside of the tree, the galah folder holds the library package)
RUN go mod download && \
    go test ./... && \
    xx-go build -trimpath -ldflags="-s -w" -o /root/galah-bin ./cmd/galah && \
    xx-verify /root/galah-bin
#
FROM alpine:3.23
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add openssl
COPY --from=builder /root/galah-bin /opt/galah/galah
COPY dist/config/ /opt/galah/config/
COPY dist/templates/ /opt/galah/templates/
COPY dist/entrypoint.sh /opt/galah/
#
# Start galah
WORKDIR /opt/galah
USER 2000:2000
CMD ["./entrypoint.sh"]
