FROM alpine

# Include dist
ADD dist/ /root/dist/

# Get and install dependencies & packages
RUN apk -U --no-cache add \
                       build-base \
                       git \
                       gmp-dev \
                       libcap \
                       libffi-dev \
                       mpc1-dev \
                       mpfr-dev \
                       openssl-dev \
                       python \
                       python-dev \
                       py-mysqldb \
                       py-pip \
                       py-requests \
                       py-setuptools && \

# Setup user
    addgroup -g 2000 cowrie && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 cowrie && \

# Install cowrie from git
    git clone --depth=1 https://github.com/micheloosterhof/cowrie /home/cowrie/cowrie/ -b v1.3.0 && \
    cd /home/cowrie/cowrie && \
    pip install --no-cache-dir --upgrade cffi pip && \
    pip install --no-cache-dir --upgrade -r requirements.txt && \

# Setup configs
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \
    cp /root/dist/cowrie.cfg /home/cowrie/cowrie/cowrie.cfg && \
    chown cowrie:cowrie -R /home/cowrie/* /usr/lib/python2.7/site-packages/twisted/plugins && \

# Start Cowrie once to prevent dropin.cache errors upon container start caused by read-only filesystem
    su - cowrie -c "export PYTHONPATH=/home/cowrie/cowrie && \
                   cd /home/cowrie/cowrie && \
                   /usr/bin/twistd --uid=2000 --gid=2000 -y cowrie.tac --pidfile cowrie.pid cowrie &" && \
    sleep 10 && \

# Clean up
    apk del --purge build-base \
                    git \
                    gmp-dev \
                    libcap \
                    libffi-dev \
                    mpc1-dev \
                    mpfr-dev \
                    python-dev \
                    py-mysqldb \
                    py-pip && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/* && \
    rm -rf /home/cowrie/cowrie/cowrie.pid

# Start cowrie
ENV PYTHONPATH /home/cowrie/cowrie
WORKDIR /home/cowrie/cowrie
USER cowrie:cowrie
CMD ["/usr/bin/twistd", "--nodaemon", "-y", "cowrie.tac", "--pidfile", "/tmp/cowrie/cowrie.pid", "cowrie"]
