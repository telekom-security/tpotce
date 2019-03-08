FROM alpine

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN sed -i 's/dl-cdn/dl-4/g' /etc/apk/repositories && \
    apk -U --no-cache add \
            build-base \
            git \
            libcap \
            python2 \
            python2-dev \
            py2-pip && \

# Upgrade pip, install virtualenv
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir virtualenv && \

# Clone honeypy from git
    git clone --depth=1 https://github.com/foospidy/HoneyPy /opt/honeypy && \
    cd /opt/honeypy && \
    virtualenv env && \
    cp /root/dist/services.cfg /opt/honeypy/etc && \
    cp /root/dist/honeypy.cfg /opt/honeypy/etc && \
    /opt/honeypy/env/bin/pip install -r /opt/honeypy/requirements.txt && \

# Setup user, groups and configs
    addgroup -g 2000 honeypy && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 honeypy && \
    chown -R honeypy:honeypy /opt/honeypy && \
    setcap cap_net_bind_service=+ep /opt/honeypy/env/bin/python2 && \

# Clean up
    apk del --purge build-base \
                    git \
                    python2-dev \
                    py2-pip && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start mailoney
#STOPSIGNAL SIGINT
USER honeypy:honeypy
WORKDIR /opt/honeypy
#EXPOSE 7 8 23 24 25 69 80 123 2048 4096 5060 9200
CMD ["/opt/honeypy/env/bin/python2", "/opt/honeypy/Honey.py", "-d"]
