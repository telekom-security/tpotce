FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Get and install dependencies & packages
RUN apk --no-cache -U add \
             bash \
             build-base \
             git \
             gmp-dev \
             libcap \
             libffi-dev \
             mpc1-dev \
             mpfr-dev \
             openssl \
             openssl-dev \
	     py3-appdirs \
	     py3-asn1-modules \
	     py3-attrs \
	     py3-bcrypt \
	     py3-cryptography \
	     py3-dateutil \
	     py3-greenlet \
	     py3-mysqlclient \
	     py3-openssl \
	     py3-packaging \
	     py3-parsing \
             py3-pip \
	     py3-service_identity \
	     py3-treq \
	     py3-twisted \
             python3 \
             python3-dev && \
#
# Setup user
    addgroup -g 2000 cowrie && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 cowrie && \
#
# Install cowrie
    mkdir -p /home/cowrie && \
    cd /home/cowrie && \
    git clone --depth=1 https://github.com/cowrie/cowrie -b v2.5.0 && \
    #git clone --depth=1 https://github.com/cowrie/cowrie && \
    cd cowrie && \
    #git checkout 8b1e1cf4db0d3b0e70b470cf40385bbbd3ed1733 && \
    mkdir -p log && \
    cp /root/dist/requirements.txt . && \
    pip3 install --upgrade pip && \
    pip3 install -r requirements.txt && \
#
# Setup configs
    export PYTHON_DIR=$(python3 --version | tr '[A-Z]' '[a-z]' | tr -d ' ' | cut -d '.' -f 1,2 ) && \
    setcap cap_net_bind_service=+ep /usr/bin/$PYTHON_DIR && \
    cp /root/dist/cowrie.cfg /home/cowrie/cowrie/cowrie.cfg && \
    chown cowrie:cowrie -R /home/cowrie/* /usr/lib/$PYTHON_DIR/site-packages/twisted/plugins && \
#
# Start Cowrie once to prevent dropin.cache errors upon container start caused by read-only filesystem
    su - cowrie -c "export PYTHONPATH=/home/cowrie/cowrie:/home/cowrie/cowrie/src && \
                    cd /home/cowrie/cowrie && \
                    /usr/bin/twistd --uid=2000 --gid=2000 -y cowrie.tac --pidfile cowrie.pid cowrie &" && \
    sleep 10 && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    gmp-dev \
                    libcap \
                    libffi-dev \
                    mpc1-dev \
                    mpfr-dev \
                    openssl-dev \
                    python3-dev \
                    py3-mysqlclient && \
    rm -rf /root/* /tmp/* && \
    rm -rf /var/cache/apk/* && \
    rm -rf /home/cowrie/cowrie/cowrie.pid && \
    rm -rf /home/cowrie/cowrie/.git && \
#    ln -s /usr/bin/python3 /usr/bin/python && \
    unset PYTHON_DIR
#
# Start cowrie
ENV PYTHONPATH /home/cowrie/cowrie:/home/cowrie/cowrie/src
WORKDIR /home/cowrie/cowrie
USER cowrie:cowrie
CMD ["/usr/bin/twistd", "--nodaemon", "-y", "cowrie.tac", "--pidfile", "/tmp/cowrie/cowrie.pid", "cowrie"]
