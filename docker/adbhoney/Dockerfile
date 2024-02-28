FROM alpine:3.19
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U add \
		git \
		procps \
		py3-psutil \
		py3-requests \
		python3 && \
#
# Install adbhoney from git
    git clone https://github.com/huuck/ADBHoney /opt/adbhoney && \
    cd /opt/adbhoney && \
#    git checkout 2417a7a982f4fd527b3a048048df9a23178767ad && \
    git checkout 42afd98611724ca3d694a48b694c957e8d953db4 && \
    cp /root/dist/adbhoney.cfg /opt/adbhoney && \
    cp /root/dist/cpu_check.py / && \
    sed -i 's/dst_ip/dest_ip/' /opt/adbhoney/adbhoney/core.py && \
    sed -i 's/dst_port/dest_port/' /opt/adbhoney/adbhoney/core.py && \
#
# Setup user, groups and configs
    addgroup -g 2000 adbhoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 adbhoney && \
    chown -R adbhoney:adbhoney /opt/adbhoney && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* /opt/adbhoney/.git /var/cache/apk/*
#
# Set workdir and start adbhoney
STOPSIGNAL SIGINT
# Adbhoney sometimes hangs at 100% CPU usage, if detected container will become unhealthy and restarted by tpotinit
HEALTHCHECK --interval=5m --timeout=30s --retries=3 CMD python3 /cpu_check.py $(pgrep -of run.py) 99
USER adbhoney:adbhoney
WORKDIR /opt/adbhoney/
CMD /usr/bin/python3 run.py
