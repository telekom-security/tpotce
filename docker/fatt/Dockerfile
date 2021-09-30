FROM alpine:3.14
#
# Get and install dependencies & packages
RUN apk -U add \
              git \
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
    git checkout 314cd1ff7873b5a145a51ec4e85f6107828a2c79 && \
    mkdir -p log && \
    # pyshark >= 0.4.3 breaks fatt
    pip3 install pyshark==0.4.2.11 && \
#
# Setup configs
    chown fatt:fatt -R /opt/fatt/* && \
#
# Clean up
    apk del --purge git \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/* 
#
# Start fatt
STOPSIGNAL SIGINT
ENV PYTHONPATH /opt/fatt
WORKDIR /opt/fatt
CMD python3 fatt.py -i $(/sbin/ip address show | /usr/bin/awk '/inet.*brd/{ print $NF; exit }') --print_output --json_logging -o log/fatt.log
