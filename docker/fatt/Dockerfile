FROM alpine:latest
#
# Include dist
#ADD dist/ /root/dist/
#
# Get and install dependencies & packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
              git \
              py3-libxml2 \
              py3-lxml \
	      py3-pip \
              python3 \
              python3-dev && \
    apk -U add tshark --repository http://dl-3.alpinelinux.org/alpine/edge/community/ && \
#
# Setup user
    addgroup -g 2000 fatt && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 fatt && \
#
# Install fatt
    mkdir -p /opt && \
    cd /opt && \
    git clone --depth=1 https://github.com/0x4D31/fatt && \
    cd fatt && \
    mkdir -p log && \
    pip3 install pyshark==0.4.2.2 && \
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
CMD python3 fatt.py -i $(/sbin/ip address | grep '^2: ' | awk '{ print $2 }' | tr -d [:punct:]) --print_output --json_logging -o log/fatt.log
