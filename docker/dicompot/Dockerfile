FROM alpine:latest
#
# Setup apk
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
                   build-base \
                   git \
                   g++ && \
    apk -U add go --repository http://dl-3.alpinelinux.org/alpine/edge/community && \
#
# Setup go, build dicompot 
    mkdir -p /opt/go && \
    export GOPATH=/opt/go/ && \
    cd /opt/go/ && \
    git clone https://github.com/nsmfoo/dicompot.git && \
    cd dicompot && \
    go mod download && \
    go install -a -x github.com/nsmfoo/dicompot/server && \
#
# Setup dicompot
    mkdir -p /opt/dicompot/images && \
    cp /opt/go/bin/server /opt/dicompot && \
#
# Setup user, groups and configs
    addgroup -g 2000 dicompot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 dicompot && \
    chown -R dicompot:dicompot /opt/dicompot && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    go \
                    g++ && \
    rm -rf /var/cache/apk/* \
           /opt/go \
           /root/dist
#
# Start dicompot
WORKDIR /opt/dicompot
USER dicompot:dicompot
CMD ["./server","-ip","0.0.0.0","-dir","images","-log","/var/log/dicompot/dicompot.log"]
