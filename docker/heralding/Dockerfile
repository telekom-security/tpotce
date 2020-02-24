FROM alpine:3.10
#  
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \ 
            		build-base \
            		git \
            		libcap \
            		libffi-dev \
            		openssl-dev \
                        libzmq \
            		postgresql-dev \
            		python3 \
            		python3-dev \
            		py-virtualenv && \
    pip3 install --no-cache-dir --upgrade pip && \
#
# Setup heralding
    mkdir -p /opt && \
    cd /opt/ && \
    git clone --depth=1 https://github.com/johnnykv/heralding && \
    cd heralding && \
    sed -i 's/asyncssh\W*/asyncssh==/' requirements.txt && \
    pip3 install --no-cache-dir -r requirements.txt && \
    pip3 install --no-cache-dir . && \
#
# Setup user, groups and configs
    export PYTHON_DIR=$(python3 --version | tr '[A-Z]' '[a-z]' | tr -d ' ' | cut -d '.' -f 1,2 ) && \
    addgroup -g 2000 heralding && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 heralding && \
    mkdir -p /var/log/heralding/ /etc/heralding && \
    mv /root/dist/heralding.yml /etc/heralding/ && \
    setcap cap_net_bind_service=+ep /usr/bin/$PYTHON_DIR && \
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
            python3-dev \
            py-virtualenv && \
    rm -rf /root/* \
           /var/cache/apk/* \
           /opt/heralding && \
    unset PYTHON_DIR
#
# Start Heralding
STOPSIGNAL SIGINT
WORKDIR /tmp/heralding/
USER heralding:heralding
CMD exec heralding -c /etc/heralding/heralding.yml -l /var/log/heralding/heralding.log
