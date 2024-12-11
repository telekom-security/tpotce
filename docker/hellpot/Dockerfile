FROM golang:1.23-alpine AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		git \
		g++ && \
#
# Setup go, hellpot
    cd /root && \
    git clone https://github.com/yunginnanet/HellPot && \
    cd HellPot && \
    git checkout c48b70110148918255f93df4fa49c675ba5a5754 && \
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
FROM scratch
#
COPY --from=builder /root/HellPot/HellPot /opt/hellpot/
COPY --from=builder /root/dist/config.toml /opt/hellpot/config/
#
# Start hellpot
WORKDIR /opt/hellpot
USER 2000:2000
CMD ["./HellPot", "-c","config/config.toml"]
