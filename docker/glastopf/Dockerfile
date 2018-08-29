FROM alpine

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U --no-cache add \
               autoconf \
               bind-tools \
               build-base \
               cython \
               git \
               libffi \
               libffi-dev \
               libcap \
               make \
               py-asn1 \
               py-cffi \
               py-chardet \
               py-cparser \
               py-cryptography \
               py-dateutil \
               py-enum34 \
               py-idna \
               py-ipaddress \
               py-jinja2 \
               py-lxml \
               py-mysqldb \
               py-openssl \
               py-pip \
               py-requests \
               py-setuptools \
               python \
               python-dev && \
    apk -U add --repository http://dl-3.alpinelinux.org/alpine/edge/testing/ \
               py-beautifulsoup4 \
               php7 \
               php7-dev \
               py-cssselect \
               py-gevent \
               py-greenlet \
               py-mongo \
               py-sqlalchemy \
               py-webob && \
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
                    py-pip \
                    re2c && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start glastopf
USER glastopf:glastopf
WORKDIR /tmp/glastopf/
CMD cp /etc/glastopf/glastopf.cfg /tmp/glastopf && exec glastopf-runner
