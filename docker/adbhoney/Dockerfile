FROM alpine 

# Install packages
RUN apk -U --no-cache add \
            git \
            libcap \
            python \
            python-dev && \

# Install adbhoney from git
    git clone --depth=1 https://github.com/huuck/ADBHoney /opt/adbhoney && \
    sed -i 's/dst_ip/dest_ip/' /opt/adbhoney/main.py && \
    sed -i 's/dst_port/dest_port/' /opt/adbhoney/main.py && \

# Setup user, groups and configs
    addgroup -g 2000 adbhoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 adbhoney && \
    chown -R adbhoney:adbhoney /opt/adbhoney && \
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \

# Clean up
    apk del --purge git \
                    python-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start adbhoney
STOPSIGNAL SIGINT
USER adbhoney:adbhoney
WORKDIR /opt/adbhoney/
CMD nohup /usr/bin/python main.py -l log/adbhoney.log -j log/adbhoney.json -d dl/
