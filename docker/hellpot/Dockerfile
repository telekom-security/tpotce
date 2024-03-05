FROM golang:1.21-alpine as builder
#
# Include dist
COPY dist/ /root/dist/
#
# Setup apk
RUN apk -U --no-cache add \
		build-base \
		git \
		g++ && \
#
# Setup go, hellpot
    cd /root && \
    git clone https://github.com/yunginnanet/HellPot && \
    cd HellPot && \
    git checkout 3673ab0228664fb3acd33102be5c7a5867137eb5 && \
    # git checkout 49433bf499b6af314786cbbc3cb8566cdb18c40c && \
    sed -i 's#logFileName := "HellPot"#logFileName := "hellpot"#g' internal/config/logger.go && \
    go mod download && \
    go vet -v ./... && \
    go test -v ./... && \
    export CGO_ENABLED=0 && \
    export VERSION=`git tag --sort=-version:refname | head -n 1` && \
    go build -trimpath \
		-ldflags "-s -w -X main.version=$VERSION" \
		cmd/HellPot/HellPot.go
#
FROM alpine:3.19
#
COPY --from=builder /root/HellPot/HellPot /opt/hellpot/
COPY --from=builder /root/dist/config.toml /opt/hellpot/config/
#
# Setup user, groups and configs
RUN addgroup -g 2000 hellpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 hellpot && \
    mkdir -p /var/log/hellpot
#
# Start hellpot
WORKDIR /opt/hellpot
USER hellpot:hellpot
CMD ["./HellPot", "-c","config/config.toml"]
