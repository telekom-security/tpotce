FROM alpine:3.20
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
		openssl-dev \
		py3-pyzmq \
		postgresql-dev \
		py3-attrs \
		py3-mysqlclient \
		py3-nose \
		py3-pip \
		py3-psycopg2 \
		py3-pycryptodome \
		py3-pyzmq \
		py3-requests \
		py3-rsa \
		py3-typing-extensions \
		py3-wheel \
		py3-yaml \
		python3 \
		python3-dev && \
#
# Setup heralding
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/johnnykv/heralding && \
    cd heralding && \
    git checkout ac12724ab38c4e2fe78f07d1bc35e6e586ba69c0 && \
    cp /root/dist/requirements.txt . && \
    pip3 install --break-system-packages --no-cache-dir --upgrade pip && \
    pip3 install --break-system-packages --no-cache-dir -r requirements.txt && \
    pip3 install --break-system-packages --no-cache-dir . && \
#
# Setup user, groups and configs
    addgroup -g 2000 heralding && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 heralding && \
    mkdir -p /var/log/heralding/ /etc/heralding && \
    mv /root/dist/heralding.yml /etc/heralding/ && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
    chown -R heralding:heralding /var/log/heralding && \
#
# Clean up
    apk del --purge \
		build-base \
		git \
		libcap \
		libffi-dev \
		openssl-dev \
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
