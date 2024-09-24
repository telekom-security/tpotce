FROM golang:1.20-alpine AS builder
#
ENV GO111MODULE=on \
    CGO_ENABLED=0 \
    GOOS=linux
#
# Install packages
RUN apk -U add git
#
WORKDIR /root
#
# Build beelzebub
RUN git clone https://github.com/t3chn0m4g3/beelzebub
WORKDIR /root/beelzebub
RUN go mod download
RUN go build -o main .
#
FROM alpine:3.20
#
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /root/beelzebub/main /opt/beelzebub/
COPY --from=builder /root/beelzebub/configurations /opt/beelzebub/configurations
#
# Setup user, groups and configs
RUN <<EOF
sed -i "s#logsPath: ./log#logsPath: ./configurations/log/beelzebub.json#g" /opt/beelzebub/configurations/beelzebub.yaml
sed -i 's/passwordRegex: "^(root|qwerty|Smoker666|123456|jenkins|minecraft|sinus|alex|postgres|Ly123456)$"/passwordRegex: ".*"/g' /opt/beelzebub/configurations/services/ssh-22.yaml
addgroup -g 2000 beelzebub
adduser -S -s /bin/ash -u 2000 -D -g 2000 beelzebub
mkdir -p /beelzebub/configurations/key \
         /beelzebub/configurations/log
chown beelzebub:beelzebub -R /opt/beelzebub/configurations
chmod 0770 -R /opt/beelzebub/configurations
EOF
#
# Start beelzebub
WORKDIR /opt/beelzebub
USER beelzebub:beelzebub
CMD ["./main"]
