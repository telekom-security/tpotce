FROM alpine:3.15
#
# Install packages
RUN apk -U --no-cache add \
               build-base \
               file \
               git \
               make \
               php7 \
               php7-dev \
	       py3-aiohttp \
               python3 \
               python3-dev \
               re2c && \
#
# Install bfr sandbox from git
    git clone https://github.com/mushorg/BFR /opt/BFR && \
    cd /opt/BFR && \
#    git checkout 508729202428a35bcc6bb27dd97b831f7e5009b5 && \
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
    git clone https://github.com/mushorg/phpox /opt/phpox && \
    cd /opt/phpox && \
    git checkout a62c8136ec7b3ebab0c989f4235e2960175121f8 && \
    make && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    php7-dev \
                    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/phpox/.git
#
# Set workdir and start phpsandbox
STOPSIGNAL SIGKILL
USER nobody:nobody
WORKDIR /opt/phpox
CMD ["python3", "sandbox.py"]
