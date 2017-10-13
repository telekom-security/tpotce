FROM alpine
MAINTAINER MO

# Add source
ADD . /opt/p0f

# Install packages
RUN apk -U upgrade && \
    apk add bash build-base git jansson-dev libpcap-dev procps && \

# Setup user, groups and configs
    addgroup -g 2000 p0f && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 p0f && \

# Download and compile p0f
    cd /opt/p0f && \
    ./build.sh && \

# Clean up
    apk del build-base git jansson-dev libpcap-dev && \
    apk add jansson libpcap && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start suricata
WORKDIR /opt/p0f
CMD /bin/bash -c "exec /opt/p0f/p0f -u p0f -j -o /var/log/p0f/p0f.json -i $(/sbin/ip address | grep '^2: ' | awk '{ print $2 }' | tr -d [:punct:])"
