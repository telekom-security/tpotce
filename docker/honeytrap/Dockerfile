FROM debian:buster-slim 
ENV DEBIAN_FRONTEND noninteractive
#
# Include dist
ADD dist/ /root/dist/
#
# Setup apt
RUN apt-get update -y && \
    apt-get dist-upgrade -y && \
#
# Install packages
    apt-get install -y autoconf \
                       build-essential \
                       git \
                       iptables \
                       libcap2 \
                       libcap2-bin \
                       libnetfilter-queue1 \
                       libnetfilter-queue-dev \
                       libjson-c-dev \
                       libtool \
                       libpq5 \
                       libpq-dev \
                       netbase \
                       procps \
                       wget && \
#
# Install honeytrap from source
    git clone https://github.com/armedpot/honeytrap /root/honeytrap && \
#    git clone https://github.com/t3chn0m4g3/honeytrap /root/honeytrap && \
    cd /root/honeytrap/ && \
    autoreconf -vfi && \
    ./configure \
      --with-stream-mon=nfq \
      --with-logattacker \
      --with-logjson \
      --prefix=/opt/honeytrap && \
    make && \
    make install && \
    make clean && \
#
# Setup user, groups and configs
    addgroup --gid 2000 honeytrap && \
    adduser --system --no-create-home --shell /bin/bash --uid 2000 --disabled-password --disabled-login --gid 2000 honeytrap && \
    mkdir -p /opt/honeytrap/etc/honeytrap/ /opt/honeytrap/var/attacks /opt/honeytrap/var/downloads /opt/honeytrap/var/log && \
    mv /root/dist/honeytrap.conf /opt/honeytrap/etc/honeytrap/ && \
    setcap cap_net_admin=+ep /opt/honeytrap/sbin/honeytrap && \
#
# Clean up
    rm -rf /root/* && \
    apt-get purge -y autoconf \
                     build-essential \
                     git \
                     libnetfilter-queue-dev \
                     libpq-dev && \
    apt-get autoremove -y --purge && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
#
# Start honeytrap
USER honeytrap:honeytrap
CMD ["/opt/honeytrap/sbin/honeytrap", "-D", "-C", "/opt/honeytrap/etc/honeytrap/honeytrap.conf", "-P", "/tmp/honeytrap/honeytrap.pid", "-t", "5", "-u", "honeytrap", "-g", "honeytrap"]
