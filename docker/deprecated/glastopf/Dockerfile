FROM alpine

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U --no-cache add \
               autoconf \
               bind-tools \
               build-base \
#               cython \
               git \
               libffi \
               libffi-dev \
               libcap \
               libxslt-dev \
               make \
               php7 \
               php7-dev \
               openssl-dev \
               py-mysqldb \
               py-openssl \
               py-pip \
               py-setuptools \
               python \
               python-dev && \
    pip install --no-cache-dir --upgrade pip && \

# Install php sandbox from git
    git clone --depth=1 https://github.com/mushorg/BFR /opt/BFR && \
    cd /opt/BFR && \
    phpize7 && \
    ./configure \
      --with-php-config=/usr/bin/php-config7 \
      --enable-bfr && \
    make && \
    make install && \
    cd / && \
    rm -rf /opt/BFR /tmp/* /var/tmp/* && \
    echo "zend_extension = "$(find /usr -name bfr.so) >> /etc/php7/php.ini && \

# Install glastopf from git
    git clone --depth=1 https://github.com/mushorg/glastopf.git /opt/glastopf && \
    cd /opt/glastopf && \
    cp /root/dist/requirements.txt . && \
    pip install --no-cache-dir . && \
    cd / && \
    rm -rf /opt/glastopf /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep /usr/bin/python2.7 && \

# Setup user, groups and configs
    addgroup -g 2000 glastopf && \
    adduser -S -H -u 2000 -D -g 2000 glastopf && \
    mkdir -p /etc/glastopf && \
    mv /root/dist/glastopf.cfg /etc/glastopf/ && \

# Clean up
    apk del --purge autoconf \
                    build-base \
                    file \
                    git \
                    libffi-dev \
                    php7-dev \
                    python-dev \
                    py-pip && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start glastopf
STOPSIGNAL SIGINT
USER glastopf:glastopf
WORKDIR /tmp/glastopf/
CMD cp /etc/glastopf/glastopf.cfg /tmp/glastopf && exec glastopf-runner
