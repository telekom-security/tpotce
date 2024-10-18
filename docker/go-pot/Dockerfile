FROM golang:1.21-alpine AS builder
RUN <<EOF
apk -U add git
mkdir -p /opt
cd /opt
git clone https://github.com/t3chn0m4g3/go-pot
EOF
WORKDIR /opt/go-pot
#
RUN go get github.com/ua-parser/uap-go/uaparser
RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux go build -o /opt/go-pot/go-pot
#
FROM alpine:3.20
#
COPY --from=builder /opt/go-pot/go-pot /opt/go-pot/go-pot
COPY --from=builder /opt/go-pot/config.yml /opt/go-pot/config.yml
#
# Setup user, groups and configs
RUN <<EOF
addgroup -g 2000 go-pot
adduser -S -s /bin/ash -u 2000 -D -g 2000 go-pot
EOF
#
STOPSIGNAL SIGINT
USER go-pot:go-pot
WORKDIR /opt/go-pot
CMD ["start", "--host", "0.0.0.0", "--config-file", "config.yml"]
ENTRYPOINT ["./go-pot"]