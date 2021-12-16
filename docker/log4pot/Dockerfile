FROM alpine:3.14
#
# Install packages
RUN apk -U add \
             build-base \
	     cargo \
             git \
	     libcap \
	     libffi-dev \
	     openssl-dev \
             python3 \
             python3-dev \
             rust && \
    apk add --no-cache -X http://dl-cdn.alpinelinux.org/alpine/edge/testing poetry && \
#	     
# Install log4pot from GitHub and setup
    mkdir -p /opt /var/log/log4pot && \
    cd /opt/ && \
    git clone https://github.com/thomaspatzke/Log4Pot && \
    cd Log4Pot && \
    git checkout 4269bf4a91457328fb64c3e7941cb2f520e5e911 && \
    sed -i 's#"type": logtype,#"reason": logtype,#g' log4pot.py && \
    poetry install && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.9 && \
#
# Setup user, groups and configs
    addgroup -g 2000 log4pot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 log4pot && \
    chown log4pot:log4pot -R /opt/Log4Pot && \
#
# Clean up
    apk del --purge build-base \
                    git \
		    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start log4pot
STOPSIGNAL SIGINT
USER log4pot:log4pot
WORKDIR /opt/Log4Pot/
CMD ["/usr/bin/python3","log4pot.py","--port","8080","--log","/var/log/log4pot/log4pot.log"]
