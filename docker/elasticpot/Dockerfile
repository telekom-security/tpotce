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
             libffi-dev \
	     openssl \
             openssl-dev \
	     postgresql-dev \
             py3-cryptography \
	     py3-elasticsearch \
	     py3-geoip2 \
	     py3-maxminddb \
             py3-mysqlclient \
	     py3-packaging \
	     py3-psycopg2 \
	     py3-redis \
             py3-requests \
	     py3-service_identity \
	     py3-setuptools \
	     py3-pip \
	     py3-twisted \
	     py3-wheel \
             python3 \
             python3-dev && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://gitlab.com/bontchev/elasticpot.git/ && \
    cd elasticpot && \
    git checkout d12649730d819bd78ea622361b6c65120173ad45 && \
    cp /root/dist/requirements.txt . && \
    pip3 install -r requirements.txt && \
#
# Setup user, groups and configs
    addgroup -g 2000 elasticpot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 elasticpot && \
    mv /root/dist/honeypot.cfg /opt/elasticpot/etc/ && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    libffi-dev \
		    openssl-dev \
		    postgresql-dev \
		    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/* /opt/elasticpot/.git
#
# Start elasticpot
STOPSIGNAL SIGINT
USER elasticpot:elasticpot
WORKDIR /opt/elasticpot/
CMD ["/usr/bin/python3","elasticpot.py"]
