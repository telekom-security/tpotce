FROM alpine:3.14
#  
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \ 
            		build-base \
            		git \
            		libcap \
            		libffi-dev \
            		openssl-dev \
                        libzmq \
            		postgresql-dev \
			py3-pip \
            		python3 \
            		python3-dev && \
#
# Setup heralding
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/johnnykv/heralding && \
    cd heralding && \
    git checkout 3f38976a2ab4d884d755b6324f2c71923ddadbdb && \
    pip3 install --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt && \
    pip3 install --no-cache-dir . && \
#
# Setup user, groups and configs
    addgroup -g 2000 heralding && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 heralding && \
    mkdir -p /var/log/heralding/ /etc/heralding && \
    mv /root/dist/heralding.yml /etc/heralding/ && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.9 && \
    chown -R heralding:heralding /var/log/heralding && \
#
# Clean up
    apk del --purge \
            build-base \
            git \
            libcap \
            libffi-dev \
            libressl-dev \
            postgresql-dev \
            python3-dev && \
    rm -rf /root/* \
           /var/cache/apk/* \
           /opt/heralding
#
# Start Heralding
STOPSIGNAL SIGINT
WORKDIR /tmp/heralding/
USER heralding:heralding
CMD exec heralding -c /etc/heralding/heralding.yml -l /var/log/heralding/heralding.log
