FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
             build-base \
	     ca-certificates \
             git \
	     libcap \
             libffi-dev \
	     openssl \
             openssl-dev \
	     postgresql-dev \
	     py3-cryptography \
	     py3-elasticsearch \
	     py3-geoip2 \
	     py3-maxminddb \
             py3-mysqlclient \
             py3-requests \
	     py3-packaging \
	     py3-pip \
	     py3-psycopg2 \
	     py3-redis \
	     py3-requests \
	     py3-service_identity \
	     py3-setuptools \
	     py3-twisted \
	     py3-wheel \
             python3 \
             python3-dev && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://gitlab.com/bontchev/ipphoney.git/ && \
    cd ipphoney && \
    git checkout 7ab1cac437baba17cb2cd25d5bb1400327e1bb79 && \
    cp /root/dist/requirements.txt . && \
    pip3 install -r requirements.txt && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Setup user, groups and configs
    addgroup -g 2000 ipphoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 ipphoney && \
    mv /root/dist/honeypot.cfg /opt/ipphoney/etc/ && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    libffi-dev \
		    openssl-dev \
		    postgresql-dev \
		    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/ipphoney/.git
#
# Start ipphoney
STOPSIGNAL SIGINT
USER ipphoney:ipphoney
WORKDIR /opt/ipphoney/
CMD ["/usr/bin/python3","ipphoney.py"]
