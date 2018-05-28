FROM alpine

# Include dist
ADD dist/ /root/dist/

# Setup apt
RUN apk -U --no-cache add \
               build-base \
               git \
               libcap \
               linux-headers \
               py3-yarl \
               python3 \
               python3-dev && \ 

# Setup Tanner
    git clone --depth=1 https://github.com/mushorg/tanner /opt/tanner && \
    cp /root/dist/config.py /opt/tanner/tanner/ && \
    cp /root/dist/requirements.txt /opt/tanner/ && \
    cd /opt/tanner/ && \
    pip3 install --no-cache-dir --upgrade pip setuptools && \
    pip3 install --no-cache-dir -r requirements.txt && \
    python3 setup.py install && \
    cd / && \
    
# Setup configs, user, groups
    addgroup -g 2000 tanner && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 tanner && \
    mkdir /var/log/tanner && \
    chown -R tanner:tanner /opt/tanner /var/log/tanner && \

# Clean up
    apk del --purge \
            build-base \
            linux-headers \
            python3-dev && \
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*

# Start conpot
USER tanner:tanner
WORKDIR /opt/tanner
CMD tanner 
