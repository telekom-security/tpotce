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
	    libstdc++ \
	    py2-markupsafe \
            python2 \
            python2-dev \
            py2-pip && \
#
# Clone honeysap from git
#    git clone --depth=1 https://github.com/SecureAuthCorp/HoneySAP /opt/honeysap && \
    git clone --depth=1 https://github.com/t3chn0m4g3/HoneySAP /opt/honeysap && \
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
#
# Clean up
    apk del --purge \
                    build-base \
                    git \
                    python2-dev && \
    rm -rf /root/* \
           /var/cache/apk/*
#
# Set workdir and start honeysap
STOPSIGNAL SIGKILL
USER honeysap:honeysap
WORKDIR /opt/honeysap
CMD ["/opt/honeysap/bin/honeysap", "--config-file", "/opt/honeysap/conf/honeysap.yml"]
