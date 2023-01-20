FROM alpine:3.17
#
# Get and install dependencies & packages
RUN apk -U --no-cache add \
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
    #git checkout 45cabf0b8b59162b99a1732d853efb01614563fe && \
    #git checkout 314cd1ff7873b5a145a51ec4e85f6107828a2c79 && \
    mkdir -p log && \
    # pyshark >= 0.4.3 breaks fatt
    pip3 install pyshark==0.4.2.11 && \
#
# Setup configs
    chgrp fatt /usr/bin/dumpcap && \
    setcap cap_net_raw,cap_net_admin=+eip /usr/bin/dumpcap && \
    chown fatt:fatt -R /opt/fatt/* && \
#
# Clean up
    apk del --purge git \
                    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/fatt/.git
#
# Start fatt
STOPSIGNAL SIGINT
ENV PYTHONPATH /opt/fatt
WORKDIR /opt/fatt
USER fatt:fatt
CMD python3 fatt.py -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }') --print_output --json_logging -o log/fatt.log
