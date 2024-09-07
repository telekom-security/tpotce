FROM ubuntu:22.04
ENV DEBIAN_FRONTEND noninteractive
ARG PROXY
ENV http_proxy=${PROXY}
#
# Include dist
COPY dist/ /root/dist/
#
# Check if APT_PROXY is set and configure apt to use the proxy only if it's available
RUN bash -c 'if [ -n "${http_proxy}" ]; then \
                 echo "Using APT proxy at ${http_proxy}"; \
                 echo "Acquire::http::Proxy \"${http_proxy}\";" > /etc/apt/apt.conf.d/01proxy; \
               else \
                 echo "APT proxy not configured, proceeding without proxy"; \
             fi' && \
#    bash -c 'echo "Acquire::http::Proxy::ports.ubuntu.com DIRECT;" > /etc/apt/apt.conf.d/99force-no-proxy' && \
# Setup apt
    apt-get update && \
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
#    git clone https://github.com/armedpot/honeytrap /root/honeytrap && \
    git clone https://github.com/t3chn0m4g3/honeytrap /root/honeytrap && \
    cd /root/honeytrap/ && \
#    git checkout 9aa4f734f2ea2f0da790b02d79afe18204a23982 && \
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
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /root/* /opt/honeytrap/.git
ENV http_proxy=""
#
# Start honeytrap
USER honeytrap:honeytrap
CMD ["/opt/honeytrap/sbin/honeytrap", "-D", "-C", "/opt/honeytrap/etc/honeytrap/honeytrap.conf", "-P", "/tmp/honeytrap/honeytrap.pid", "-t", "5", "-u", "honeytrap", "-g", "honeytrap"]
