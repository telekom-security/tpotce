FROM golang:1.21-alpine as builder
#
# Include dist
COPY dist/ /root/dist/
#
# Setup apk
RUN apk -U add --no-cache \
		build-base \
		git \
		g++ && \
#
# Setup go, build dicompot 
    mkdir -p /opt/go && \
    export GOPATH=/opt/go/ && \
    cd /opt/go/ && \
    git clone https://github.com/nsmfoo/dicompot.git && \
    cd dicompot && \
    git checkout 41331194156bbb17078bcc1594f4952ac06a731e && \
    go mod download && \
    go install -a -x github.com/nsmfoo/dicompot/server
#
FROM alpine:3.19
#
# Setup dicompot
#
COPY --from=builder /opt/go/bin/server /opt/dicompot/server
COPY --from=builder /root/dist/dcm_pts/images /opt/dicompot/images
#
# Setup user, groups and configs
#
RUN addgroup -g 2000 dicompot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 dicompot && \
    chown -R dicompot:dicompot /opt/dicompot
#
# Start dicompot
WORKDIR /opt/dicompot
USER dicompot:dicompot
CMD ["./server","-ip","0.0.0.0","-dir","images","-log","/var/log/dicompot/dicompot.log"]
