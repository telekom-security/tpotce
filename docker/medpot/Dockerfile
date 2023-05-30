FROM alpine:3.17
#
# Setup apk
RUN apk -U --no-cache add \
                   build-base \
                   git \
                   go \
                   g++ && \
#
# Setup go, build medpot
    export GOPATH=/tmp && \
    export GO111MODULE=off && \
    cd /tmp && \
    go get -d -v github.com/davecgh/go-spew/spew && \
    go get -d -v github.com/go-ini/ini && \
    go get -d -v github.com/mozillazg/request && \
    go get -d -v go.uber.org/zap && \
    go get -d -v github.com/s9rA16Bf4/ArgumentParser/go/arguments && \
    go get -d -v github.com/s9rA16Bf4/notify_handler/go/notify && \
#    git clone https://github.com/schmalle/medpot && \
    git clone https://github.com/s9rA16Bf4/medpot && \
    cd medpot && \
#    git checkout dd9833786bb56cd40c11dfb15e0dd57298e249e8 && \
    git checkout 0feb786cd8a7616498ba9749dbfda24b5dbd363e && \
    sed -i s/"ews = true"/"ews = false"/g template/ews.cfg && \
    go build -o medpot go/medpot.go go/logo.go && \
#
# Setup medpot
    mkdir -p /etc/medpot \
             /opt/medpot \
             /var/log/medpot && \
    cp medpot /opt/medpot && \
    cp ./template/* /etc/medpot && \
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
           /root/dist \
	   /tmp
#
# Start medpot
WORKDIR /opt/medpot
USER medpot:medpot
CMD ["./medpot"]
