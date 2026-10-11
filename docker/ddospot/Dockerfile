# syntax=docker/dockerfile:1
#
# The fetch stage gets ddospot v2 at a pinned commit into the stage src, a local
# checkout can replace src with --build-context src=<path>
FROM --platform=$BUILDPLATFORM alpine:3.24 AS fetch
RUN apk --no-cache add git ca-certificates
WORKDIR /src
RUN git -c init.defaultBranch=main init --quiet && \
    git remote add origin https://github.com/t3chn0m4g3/ddospot && \
    git fetch --quiet --depth 1 origin 93d5550213d568dd56a78e15fa8f10b50e8954e9 && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD
FROM scratch AS src
COPY --from=fetch /src/ /
#
# The builder runs on the build platform and cross-compiles, Go under QEMU
# emulation crashes
FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS builder
ARG TARGETOS
ARG TARGETARCH
RUN apk --no-cache add ca-certificates
COPY --from=src / /src
WORKDIR /src
RUN go mod download && \
    go test ./... && \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /ddospot ./cmd/ddospot && \
    mkdir -p /logdir
#
FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /ddospot /ddospot
# The log folder belongs to the user, so a volume mounted there starts
# writable for it
COPY --from=builder --chown=2000:2000 /logdir /var/log/ddospot
#
# Start ddospot
STOPSIGNAL SIGTERM
USER 2000:2000
ENTRYPOINT ["/ddospot"]
