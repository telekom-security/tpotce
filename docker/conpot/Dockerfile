FROM alpine:3.24
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
            python3 \
            python3-dev \
            py3-pip \
            py3-tomli-w && \
#
# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot && \
    cd /opt/conpot/ && \
    git checkout 3be619a868982df7d584e8e2865ad74d9b4eb409 && \
    # T-Pot hooks (see dist/tpot.patch), then T-Pot templates and overrides
    git apply /root/dist/tpot.patch && \
    python3 /root/dist/tpot/tpot_templates.py build conpot/templates /root/dist/tpot/templates.toml && \
    pip3 install --break-system-packages --no-cache-dir . && \
    python3 /root/dist/tpot/tpot_templates.py check /root/dist/tpot/templates.toml && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Setup configs, user, groups
    mkdir -p /etc/conpot /opt/tpot /var/lib/conpot /var/log/conpot && \
    sed "s#@@PURELIB@@#$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')#" /root/dist/conpot.cfg > /etc/conpot/conpot.cfg && \
    cp /root/dist/tpot/tpot_templates.py /root/dist/tpot/templates.toml /opt/tpot/ && \
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 conpot && \
    chown conpot:conpot /var/lib/conpot && \
#
# Clean up
    apk del --purge \
            build-base \
            git \
            libcap \
            libffi-dev \
            py3-pip \
            py3-tomli-w \
            python3-dev && \
    rm -rf /root/* \
           /tmp/* \
           /var/cache/apk/*
#
# Start conpot
STOPSIGNAL SIGINT
ENV TMPDIR=/tmp/conpot
USER conpot:conpot
CMD ["/bin/sh", "-c", "exec python3 /opt/tpot/tpot_templates.py render \"$CONPOT_TEMPLATE\""]
