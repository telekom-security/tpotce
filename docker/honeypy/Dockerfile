FROM alpine:3.11
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
            py2-pip && \
#
# Install virtualenv
    pip install --no-cache-dir virtualenv && \
#
# Clone honeypy from git
    git clone https://github.com/foospidy/HoneyPy /opt/honeypy && \
    cd /opt/honeypy && \
    git checkout feccab56ca922bcab01cac4ffd82f588d61ab1c5 && \
    sed -i 's/local_host/dest_ip/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/local_port/dest_port/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/remote_host/src_ip/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/remote_port/src_port/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/service/proto/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/event/event_type/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/bytes/size/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/date_time/timestamp/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/data,/data.decode("hex"),/g' /opt/honeypy/loggers/file/honeypy_file.py && \
    sed -i 's/urllib3/urllib3 == 1.21.1/g' /opt/honeypy/requirements.txt && \
    virtualenv env && \
    cp /root/dist/services.cfg /opt/honeypy/etc && \
    cp /root/dist/honeypy.cfg /opt/honeypy/etc && \
    /opt/honeypy/env/bin/pip install -r /opt/honeypy/requirements.txt && \
#
# Setup user, groups and configs
    addgroup -g 2000 honeypy && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 honeypy && \
    chown -R honeypy:honeypy /opt/honeypy && \
    setcap cap_net_bind_service=+ep /opt/honeypy/env/bin/python && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    python2-dev \
                    py2-pip && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start honeypy
USER honeypy:honeypy
WORKDIR /opt/honeypy
CMD ["/opt/honeypy/env/bin/python2", "/opt/honeypy/Honey.py", "-d"]
