FROM golang:1.23-alpine AS builder
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
RUN git clone https://github.com/t3chn0m4g3/beelzebub && \
    cd beelzebub && \
    git checkout 0b9aba53ec1671f669d22782758142a1d411b858
WORKDIR /root/beelzebub
RUN go mod download
RUN go build -o main .
RUN sed -i "s#logsPath: ./log#logsPath: ./configurations/log/beelzebub.json#g" /root/beelzebub/configurations/beelzebub.yaml
RUN sed -i 's/passwordRegex: "^(root|qwerty|Smoker666|123456|jenkins|minecraft|sinus|alex|postgres|Ly123456)$"/passwordRegex: ".*"/g' /root/beelzebub/configurations/services/ssh-22.yaml
#
FROM scratch
#
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /root/beelzebub/main /opt/beelzebub/
COPY --from=builder /root/beelzebub/configurations /opt/beelzebub/configurations
#
# Start beelzebub
WORKDIR /opt/beelzebub
USER 2000:2000
ENTRYPOINT ["./main"]
