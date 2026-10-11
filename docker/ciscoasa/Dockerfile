# Override the named "source" context for a local pre-publication build.
FROM --platform=$BUILDPLATFORM alpine:3.24 AS fetch
ARG CISCOASA_TAG=v2.0.1
ARG CISCOASA_COMMIT=d05a195dff9816014eaf2d6ff63a73e7b5aa17c4
RUN apk add --no-cache ca-certificates git && \
    git clone --branch "$CISCOASA_TAG" --depth 1 https://github.com/t3chn0m4g3/ciscoasa_honeypot /src && \
    test "$(git -C /src rev-parse HEAD)" = "$CISCOASA_COMMIT"

FROM scratch AS source
COPY --from=fetch /src/ /

FROM --platform=$BUILDPLATFORM golang:1.26.4-alpine3.24 AS builder
ARG TARGETARCH
WORKDIR /src
COPY --from=source /go.mod ./
COPY --from=source /cmd/ cmd/
COPY --from=source /internal/ internal/
RUN CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/asapot ./cmd/asapot

FROM scratch
COPY --from=builder /out/asapot /asapot
USER 2000:2000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/asapot"]
