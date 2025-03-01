FROM golang:1.23-alpine AS builder
RUN <<EOF
apk -U upgrade
apk -U add git
mkdir -p /opt
cd /opt
git clone https://github.com/ryanolee/go-pot -b v1.0.0
EOF
WORKDIR /opt/go-pot
#
RUN CGO_ENABLED=0 GOOS=linux go build -o /opt/go-pot/go-pot
#
FROM scratch
#
COPY --from=builder /opt/go-pot/go-pot /opt/go-pot/go-pot
COPY dist/config.yml /opt/go-pot/config.yml
#
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/go-pot
CMD ["start", "--host", "0.0.0.0", "--config-file", "config.yml"]
ENTRYPOINT ["./go-pot"]