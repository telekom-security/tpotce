FROM alpine:3.12
#
# Include dist
ADD dist/ /root/dist/
#
# Setup apt
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
               build-base \
               git \
               libcap \
               libffi-dev \
               libressl-dev \
               linux-headers \
	       py3-pip \
               py3-yarl \
               python3 \
               python3-dev && \ 
#
# Setup Tanner
    git clone https://github.com/mushorg/tanner /opt/tanner && \
    cd /opt/tanner/ && \
#    git fetch origin pull/364/head:test && \	
#    git checkout test && \
    git checkout 40e2357119065445cbb06234e953a95e5a73ce93 && \
    cp /root/dist/config.yaml /opt/tanner/tanner/data && \
    pip3 install --no-cache-dir setuptools && \
    pip3 install --no-cache-dir -r requirements.txt && \
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
            libressl-dev \
            linux-headers \
            python3-dev && \
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*
#
# Start tanner
STOPSIGNAL SIGKILL
USER tanner:tanner
WORKDIR /opt/tanner
CMD tanner 
