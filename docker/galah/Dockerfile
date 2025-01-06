FROM golang:1.23-alpine AS builder
RUN <<EOF
apk --no-cache -U upgrade
apk --no-cache -U add build-base git
mkdir -p /opt
cd /opt
git clone https://github.com/t3chn0m4g3/galah
cd galah
git checkout d4739ec5abaed83c5367716a77908be548d3d003
EOF
WORKDIR /opt/galah
ENV CGO_ENABLED=1
RUN <<EOF
go mod download
go build -o galah ./cmd/galah
EOF
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add openssl
COPY --from=builder /opt/galah/ /opt/galah/
#
# Start galah
WORKDIR /opt/galah
USER 2000:2000
CMD ["./entrypoint.sh"]
