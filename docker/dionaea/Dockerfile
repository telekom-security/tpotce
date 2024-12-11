FROM ubuntu:24.04
ENV DEBIAN_FRONTEND noninteractive
#
# Include dist
COPY dist/ /root/dist/
#
# Determine arch, get and install packages
RUN ARCH=$(arch) && \
      if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi && \
      if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi && \
    echo "$ARCH" && \
    cd /root/dist/ && \
# Setup apt
    apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install wget -y && \
    wget http://ftp.us.debian.org/debian/pool/main/libe/libemu/libemu2_0.2.0+git20120122-1.2+b1_$ARCH.deb \
         http://ftp.us.debian.org/debian/pool/main/libe/libemu/libemu-dev_0.2.0+git20120122-1.2+b1_$ARCH.deb && \
    apt-get install ./libemu2_0.2.0+git20120122-1.2+b1_$ARCH.deb \
                ./libemu-dev_0.2.0+git20120122-1.2+b1_$ARCH.deb -y && \
    apt-get install -y --no-install-recommends \
		build-essential \
		ca-certificates \
		check \
		cmake \
		cython3 \
		git \
		libcap2-bin \
		libcurl4-openssl-dev \
		libev-dev \
		libglib2.0-dev \
		libloudmouth1-dev \
		libnetfilter-queue-dev \
		libnl-3-dev \
		libpcap-dev \
		libssl-dev \
		libtool \
		libudns-dev \
		procps \
		python3 \
		python3-dev \
		python3-boto3 \
		python3-bson \
		python3-setuptools \
		python3-yaml \
		fonts-liberation && \
#
# Get and install dionaea
    git clone https://github.com/t3chn0m4g3/dionaea -b 0.11.1 /root/dionaea/ && \
    cd /root/dionaea && \
    mkdir build && \
    cd build && \
    cmake -DCMAKE_INSTALL_PREFIX:PATH=/opt/dionaea .. && \
    make && \
    make install && \
#
# Setup user and groups
    addgroup --gid 2000 dionaea && \
    adduser --system --no-create-home --shell /bin/bash --uid 2000 --disabled-password --disabled-login --gid 2000 dionaea && \
    setcap cap_net_bind_service=+ep /opt/dionaea/sbin/dionaea && \
#
# Supply configs and set permissions
    chown -R dionaea:dionaea /opt/dionaea/var && \
    rm -rf /opt/dionaea/etc/dionaea/* && \
    mv /root/dist/etc/* /opt/dionaea/etc/dionaea/ && \
    cp /root/dist/cpu_check.py / && \
#
# Setup runtime and clean up
    apt-get purge -y \
      build-essential \
      ca-certificates \
      check \
      cmake \
      cython3 \
      git \
      libcurl4-openssl-dev \
      libemu-dev \
      libev-dev \
      libglib2.0-dev \
      libloudmouth1-dev \
      libnetfilter-queue-dev \
      libnl-3-dev \
      libpcap-dev \
      libssl-dev \
      libtool \
      libudns-dev \
      python3 \
      python3-dev \   
      python3-boto3 \
      python3-bson \
      python3-yaml \ 
      wget && \ 
#
    apt-get install -y \
      ca-certificates \
      python3 \
      python3-boto3 \
      python3-bson \
      python3-psutil \
      python3-yaml \
      libcurl4 \
      libemu2 \
      libev4 \
      libglib2.0-0 \
      libnetfilter-queue1 \
      libnl-3-200 \
      libpcap0.8 \
      libpython3.12 \
      libudns0 && \
#
    apt-get autoremove --purge -y && \
    apt-get clean && \
    rm -rf /root/* \
           /var/lib/apt/lists/* \
           /tmp/* \
           /var/tmp/* \
           /root/.cache \
           /opt/dionaea/.git
#
# Start dionaea
STOPSIGNAL SIGINT
#
# Dionaea sometimes hangs at 100% CPU usage, if detected container will become unhealthy and restarted by tpotinit
HEALTHCHECK --interval=5m --timeout=30s --retries=3 CMD python3 /cpu_check.py $(pgrep -of dionaea) 99
USER dionaea:dionaea
CMD ["/opt/dionaea/sbin/dionaea", "-u", "dionaea", "-g", "dionaea", "-c", "/opt/dionaea/etc/dionaea/dionaea.cfg"]
