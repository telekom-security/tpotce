FROM alpine:3.20
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		git \
		libcap \
		py3-libxml2 \
		py3-lxml \
		py3-pip \
		python3 \
		python3-dev \
		tshark && \
#
# Setup user
    addgroup -g 2000 fatt && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 fatt && \
#
# Install fatt
    mkdir -p /opt && \
    cd /opt && \
    git clone https://github.com/0x4D31/fatt && \
    cd fatt && \
    git checkout c29e553514281e50781f86932b82337a5ada5640 && \
    mkdir -p log && \
    pip3 install --no-cache-dir --break-system-packages pyshark && \
#
# Setup configs
    chgrp fatt /usr/bin/dumpcap && \
    setcap cap_net_raw,cap_net_admin=+eip /usr/bin/dumpcap && \
    chown fatt:fatt -R /opt/fatt/* && \
#
# Clean up
    apk del --purge git \
            python3-dev && \
    rm -rf /root/* \
            /var/cache/apk/* \
            /opt/fatt/.git
#
# Start fatt
STOPSIGNAL SIGINT
ENV PYTHONPATH /opt/fatt
WORKDIR /opt/fatt
USER fatt:fatt
CMD python3 fatt.py -i $(ip route | grep "^default" | awk '{ print $5 }') --print_output --json_logging -o log/fatt.log
