FROM alpine:3.10
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
               build-base \
               file \
               git \
               make \
               php7 \
               php7-dev \
	       py3-pip \
               python3 \
               python3-dev \
               re2c && \
#
# Install bfr sandbox from git
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
#
# Install PHP Sandbox
    git clone --depth=1 https://github.com/mushorg/phpox /opt/phpox && \
    cd /opt/phpox && \
    pip3 install -r requirements.txt && \
    make && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    php7-dev \
                    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start phpsandbox
STOPSIGNAL SIGKILL
USER nobody:nobody
WORKDIR /opt/phpox
CMD ["python3", "sandbox.py"]
