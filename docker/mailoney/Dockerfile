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
    git clone https://github.com/buffer/libemu /root/libemu/ && \
    cd /root/libemu/ && \
    git checkout e2624361e13588da74a2ce3e1dea0abb59dcf1d0 && \
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
    git clone https://github.com/t3chn0m4g3/mailoney /opt/mailoney && \
    cd /opt/mailoney && \
    git checkout 85c37649a99e1cec3f8d48d509653c9a8127ea4f && \
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
