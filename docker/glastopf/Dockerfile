FROM alpine 
MAINTAINER MO 

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U upgrade && \
    apk add autoconf bash bind-tools build-base cython git libffi libffi-dev make py-asn1 \
            py-cffi py-chardet py-chardet py-cparser py-cryptography py-dateutil \
            py-enum34 py-idna py-ipaddress py-jinja2 py-lxml py-mysqldb py-openssl \
            py-pip py-requests py-setuptools python python-dev && \
    apk -U add --repository http://dl-3.alpinelinux.org/alpine/edge/testing/ \
            py-beautifulsoup4 php7 php7-dev py-cssselect py-gevent py-greenlet py-mongo \
            py-sqlalchemy py-webob && \

# Install php sandbox from git
    git clone https://github.com/glastopf/BFR.git /opt/BFR && \
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
    git clone https://github.com/mushorg/glastopf.git /opt/glastopf && \
    cd /opt/glastopf && \
    python setup.py install && \
    cd / && \
    rm -rf /opt/glastopf /tmp/* /var/tmp/* && \

# Setup user, groups and configs
    addgroup -g 2000 glastopf && \
    adduser -S -H -u 2000 -D -g 2000 glastopf && \
    mkdir -p /opt/glastopf && \
    mv /root/dist/glastopf.cfg /opt/glastopf/ && \

# Clean up
    apk del autoconf build-base git libffi-dev php7-dev python-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start glastopf
WORKDIR /opt/glastopf/
CMD ["glastopf-runner"]
