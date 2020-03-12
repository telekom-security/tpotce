FROM alpine:latest
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
            python2 \
            python2-dev \
            py2-pip \
            tcpdump && \
#
# Clone honeysap from git
    git clone --depth=1 https://github.com/SecureAuthCorp/HoneySAP /opt/honeysap && \
    cd /opt/honeysap && \
    mkdir conf && \
    cp /root/dist/* conf/ && \
    python setup.py install && \
    pip install -r requirements-optional.txt && \
#
# Setup user, groups and configs
    addgroup -g 2000 honeysap && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 honeysap && \
    chown -R honeysap:honeysap /opt/honeysap && \
#    setcap cap_net_bind_service=+ep /opt/honeypy/env/bin/python && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* \
           /var/cache/apk/*
#
# Set workdir and start honeysap
USER honeysap:honeysap
WORKDIR /opt/honeysap
CMD ["/opt/honeysap/bin/honeysap", "--config-file", "/opt/honeysap/conf/honeysap.yml"]
