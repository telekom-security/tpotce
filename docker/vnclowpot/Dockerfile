FROM alpine
MAINTAINER MO

# Include dist
#ADD dist/ /root/dist/

# Setup apk
RUN apk -U add bash \
               build-base \
               git \
               go \
               procps && \

# Setup vnclowpot
    go get github.com/magisterquis/vnclowpot && \
    go install github.com/magisterquis/vnclowpot && \

# Setup user, groups and configs
    addgroup -g 2000 vnclowpot  && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 vnclowpot && \
    mkdir -p /var/log/vnclowpot && \

# Clean up
    apk del build-base \
            git && \
    rm -rf /var/cache/apk/*

# Run supervisor upon container start
CMD /root/go/bin/vnclowpot -j >> /var/log/vnclowpot/vnclowpot.log
