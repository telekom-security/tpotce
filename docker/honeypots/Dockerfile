FROM alpine:3.20
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
	apk --no-cache -U add \
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
		py3-itsdangerous \
		py3-jinja2 \
		py3-ldap3 \
		py3-markupsafe \
		py3-openssl \
		py3-packaging \
		py3-pip \
		py3-psycopg2 \
		py3-pycryptodomex \
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
	# git checkout 238c6732d28b36ab88f2008361bafc8aef054755 && \
	git checkout 3f52129fbe9a712e86c54207ceb088c3b2c2af17 && \
    pip3 install --break-system-packages --no-cache-dir . && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
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
		py3-pip \
		python3-dev \
		zlib-dev && \
	rm -rf /root/* \
			/var/cache/apk/* \
			/opt/honeypots/.git
#
# Start honeypots 
STOPSIGNAL SIGINT
USER honeypots:honeypots
WORKDIR /opt/honeypots/
CMD python3 -E -m honeypots --setup all --config config.json
