FROM alpine:3.23
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		git \
		libcap \
		libffi-dev \
		openssl-dev \
		linux-headers \
		py3-aiohttp \
		py3-geoip2 \
		py3-jinja2 \
		py3-jwt \
		py3-mako \
		py3-mysqlclient \
		py3-packaging \
		py3-pip \
		py3-redis \
		py3-pycodestyle \
		py3-setuptools \
		py3-tornado \
		py3-websocket-client \
		py3-wheel \
		py3-yaml \
		py3-yarl \
		python3 \
		python3-dev && \ 
#
# Setup Tanner
	git clone https://github.com/mushorg/tanner /opt/tanner && \
    cd /opt/tanner/ && \
    # git checkout 245d628f4023e5462c7035e760a6b5eed0a33fbe && \
    git checkout 3bc9ae2831db2d7e9c3aa3fb98b3b3a6fc73b18c && \
    cp /root/dist/config.yaml /opt/tanner/tanner/data && \
    cp /root/dist/requirements.txt . && \
    pip3 install --break-system-packages --no-cache-dir cython && \
    pip3 install --break-system-packages --no-cache-dir --no-build-isolation pylibinjection && \
    pip3 install --break-system-packages --no-cache-dir -r requirements.txt && \
    sed -i 's/class TimeoutError(asyncio.TimeoutError, builtins.TimeoutError, RedisError):/class TimeoutError(asyncio.TimeoutError, RedisError):/' /usr/lib/python*/site-packages/aioredis/exceptions.py && \
    python3 setup.py install && \
    rm -rf .coveragerc \
           .git \
           .gitignore \
           .travis.yml \
           Tanner.egg-info \
           build \
           dist \
           docker \
           docs \
           requirements.txt \
           setup.py && \
    cd / && \
#   
# Setup configs, user, groups
    addgroup -g 2000 tanner && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 tanner && \
    mkdir /var/log/tanner && \
    chown -R tanner:tanner /opt/tanner /var/log/tanner && \
#
# Clean up
    apk del --purge \
            build-base \
            git \
            libcap \
            libffi-dev \
            openssl-dev \
            linux-headers \
            python3-dev && \
    rm -rf /root/* \
           /tmp/* \
           /var/tmp/* \
           /var/cache/apk/* \
           /opt/tanner/.git
#
# Start tanner
STOPSIGNAL SIGKILL
USER tanner:tanner
WORKDIR /opt/tanner
CMD ["tanner"]
