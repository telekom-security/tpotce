FROM alpine:3.12
#
# Setup apk
RUN apk -U --no-cache add \
                   build-base \
                   git \
                   go \
                   g++ && \
#
# Setup go, build medpot
    export GOPATH=/opt/go/ && \
    mkdir -p /opt/go/src && \
    cd /opt/go/src && \
    git clone https://github.com/schmalle/medpot && \
    cd medpot && \
    git checkout 75a2e6134cf926c35b6017d62542274434c87388 && \
    cd .. && \
    go get -d -v github.com/davecgh/go-spew/spew && \
    go get -d -v github.com/go-ini/ini && \
    go get -d -v github.com/mozillazg/request && \
    go get -d -v go.uber.org/zap && \
    cd medpot && \
    cp dist/etc/ews.cfg /etc/ && \
    go build medpot && \
#
# Setup medpot
    mkdir -p /opt/medpot \
             /var/log/medpot && \
    cp medpot /opt/medpot && \
    cp /opt/go/src/medpot/template/*.xml /opt/medpot/ && \
#
# Setup user, groups and configs
    addgroup -g 2000 medpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 medpot && \
    chown -R medpot:medpot /var/log/medpot && \
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
# Start medpot
WORKDIR /opt/medpot
USER medpot:medpot
CMD ["./medpot"]
