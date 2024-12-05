FROM golang:1.23-alpine AS builder
#
ENV GO111MODULE=on \
    CGO_ENABLED=0 \
    GOOS=linux
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
        build-base \
        git \
        g++
#
# Setup go, build medpot
RUN mkdir -p /usr/local/go/src/medpot && \
    git clone https://github.com/schmalle/medpot /usr/local/go/src/medpot && \
    cd /usr/local/go/src/medpot && \
    git checkout 75a2e6134cf926c35b6017d62542274434c87388 && \
    go mod init github.com/schmalle/medpot && \
    go mod tidy && \
    go mod download && \
    cp dist/etc/ews.cfg /etc/ && \
    go build medpot
#
FROM scratch
#
# Setup medpot
#
COPY --from=builder /usr/local/go/src/medpot/medpot /opt/medpot/medpot
COPY --from=builder /usr/local/go/src/medpot/template/*.xml /opt/medpot/
COPY --from=builder /usr/local/go/src/medpot/dist/etc/ews.cfg /etc/ews.cfg
#
# Start medpot
WORKDIR /opt/medpot
USER 2000:2000
CMD ["./medpot"]
