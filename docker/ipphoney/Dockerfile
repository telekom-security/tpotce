FROM alpine:3.20 AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
	apk --no-cache -U add \
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
		py3-tz \
		py3-wheel \
		python3 \
		python3-dev && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://gitlab.com/bontchev/ipphoney.git/ && \
    cd ipphoney && \
    git checkout 9da0a9e7f9d839e4522f56b3bb2b790d87d65457 && \
    cp /root/dist/requirements.txt .
WORKDIR /opt/ipphoney
RUN pip3 install --break-system-packages pyinstaller
RUN pip3 install --break-system-packages -r requirements.txt
RUN pyinstaller ipphoney.py \
		--hidden-import output_plugins \
		--hidden-import output_plugins.jsonlog
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/ipphoney/dist/ /opt/
COPY --from=builder /opt/ipphoney/responses/ /opt/ipphoney/responses/
COPY dist/honeypot.cfg /opt/ipphoney/etc/
#
# Start ipphoney
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/ipphoney/
CMD ["./ipphoney"]
