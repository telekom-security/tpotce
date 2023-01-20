FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
             build-base \
	     freetds \
	     freetds-dev \
	     gcc \
             git \
             hiredis \
	     jpeg-dev \
	     libcap \
             libffi-dev \
             libpq \
	     musl-dev \
             openssl \
             openssl-dev \
	     postgresql-dev \
	     py3-chardet \
	     py3-click \
	     py3-cryptography \
	     py3-dnspython \
	     py3-flask \
	     py3-future \
	     py3-hiredis \
	     py3-impacket \
	     py3-itsdangerous \
	     py3-jinja2 \
	     py3-ldap3 \
	     py3-markupsafe \
	     py3-netifaces \
	     py3-openssl \
	     py3-packaging \
	     py3-paramiko \
	     py3-pip \
	     py3-psutil \
	     py3-psycopg2 \
	     py3-pycryptodomex \
	     py3-requests \
	     py3-service_identity \
	     py3-twisted \
	     py3-werkzeug \
	     py3-wheel \
             python3 \
             python3-dev \
             zlib-dev && \
#	     
# Install honeypots from GitHub and setup
    mkdir -p /opt \
             /var/log/honeypots && \
    cd /opt/ && \
    git clone https://github.com/qeeqbox/honeypots && \
    cd honeypots && \
#    git checkout bee3147cf81837ba7639f1e27fe34d717ecccf29 && \
    git checkout 1ad37d7e07838e9ad18c5244d87b9e49d90c9bc3 && \
    cp /root/dist/setup.py . && \
    pip3 install --upgrade pip && \
    pip3 install . && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Setup user, groups and configs
    addgroup -g 2000 honeypots && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 honeypots && \
    chown honeypots:honeypots -R /opt/honeypots && \
    chown honeypots:honeypots -R /var/log/honeypots && \
    mv /root/dist/config.json /opt/honeypots/ && \
#
# Clean up
    apk del --purge build-base \
                    freetds-dev \
                    git \
		    jpeg-dev \
		    libffi-dev \
		    openssl-dev \
		    postgresql-dev \
		    python3-dev \
		    zlib-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/honeypots/.git

#
# Start honeypots 
STOPSIGNAL SIGINT
USER honeypots:honeypots
WORKDIR /opt/honeypots/
CMD python3 -E -m honeypots --setup all --config config.json
