FROM alpine:3.11
#
# Install packages
RUN apk -U --no-cache add \
            autoconf \
            automake \
            build-base \
            git \
            libcap \
            libtool \
            py-pip \
            python \
            python-dev && \
#
# Install libemu    
    git clone --depth=1 https://github.com/buffer/libemu /root/libemu/ && \
    cd /root/libemu/ && \
    autoreconf -vi && \
    ./configure && \
    make && \
    make install && \
#
# Install libemu python wrapper
    pip install --no-cache-dir \ 
                        hpfeeds \
                        pylibemu && \ 
#
# Install mailoney from git
    git clone --depth=1 https://github.com/t3chn0m4g3/mailoney /opt/mailoney && \
#
# Setup user, groups and configs
    addgroup -g 2000 mailoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 mailoney && \
    chown -R mailoney:mailoney /opt/mailoney && \
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \
#
# Clean up
    apk del --purge autoconf \
                    automake \
                    build-base \
                    git \
                    py-pip \
                    python-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start mailoney
STOPSIGNAL SIGINT
USER mailoney:mailoney
WORKDIR /opt/mailoney/
CMD ["/usr/bin/python","mailoney.py","-i","0.0.0.0","-p","25","-s","mailrelay.local","-t","schizo_open_relay"]
