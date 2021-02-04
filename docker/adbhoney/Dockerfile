FROM alpine:3.12
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
            git \
            libcap \
	    py3-pip \
            python3 \
            python3-dev && \
#
# Install adbhoney from git
    git clone https://github.com/huuck/ADBHoney /opt/adbhoney && \
    cd /opt/adbhoney && \
    git checkout ad7c17e78d01f6860d58ba826a4b6a4e4f83acbd && \
    cp /root/dist/adbhoney.cfg /opt/adbhoney && \
    sed -i 's/dst_ip/dest_ip/' /opt/adbhoney/adbhoney/core.py && \
    sed -i 's/dst_port/dest_port/' /opt/adbhoney/adbhoney/core.py && \
#
# Setup user, groups and configs
    addgroup -g 2000 adbhoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 adbhoney && \
    chown -R adbhoney:adbhoney /opt/adbhoney && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.8 && \
#
# Clean up
    apk del --purge git \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start adbhoney
STOPSIGNAL SIGINT
USER adbhoney:adbhoney
WORKDIR /opt/adbhoney/
CMD nohup /usr/bin/python3 run.py
