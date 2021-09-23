FROM debian:buster-slim
#
# Install packages
RUN apt-get update -y && \
    apt-get install -y \
            autoconf \
	    automake \
            build-essential \
            git \
            libcap2-bin \
	    libtool \
	    python2 \
	    python2-dev \
            python-pip && \
#
# Install libemu    
    git clone https://github.com/buffer/libemu /root/libemu/ && \
    cd /root/libemu/ && \
    git checkout e2624361e13588da74a2ce3e1dea0abb59dcf1d0 && \
    autoreconf -vi && \
    ./configure && \
    make && \
    make install && \
#
# Install libemu python wrapper
    pip install --no-cache-dir \ 
                        hpfeeds \
                        pylibemu && \ 
#
# Install mailoney from git
    git clone https://github.com/t3chn0m4g3/mailoney /opt/mailoney && \
    cd /opt/mailoney && \
    git checkout 85c37649a99e1cec3f8d48d509653c9a8127ea4f && \
#
# Setup user, groups and configs
    addgroup --gid 2000 mailoney && \
    adduser --system --no-create-home --shell /bin/bash --uid 2000 --disabled-password --disabled-login --gid 2000 mailoney && \
    chown -R mailoney:mailoney /opt/mailoney && \
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \
#
# Clean up
    apt-get purge -y \
            autoconf \
	    automake \
            build-essential \
	    git \
	    libtool \
	    python2-dev \
	    python-pip && \
    apt-get autoremove --purge -y && \
    apt-get clean && \
    rm -rf /root/* /var/lib/apt/lists/* /tmp/* /var/tmp/*
#
# Set workdir and start mailoney
STOPSIGNAL SIGINT
USER mailoney:mailoney
WORKDIR /opt/mailoney/
CMD ["/usr/bin/python2","mailoney.py","-i","0.0.0.0","-p","25","-s","mailrelay.local","-t","schizo_open_relay"]
