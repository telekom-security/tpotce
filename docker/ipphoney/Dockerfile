FROM alpine:3.23 AS builder
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
    git clone https://gitlab.com/bontchev/ipphoney.git/ -b v2.0.2 && \
    cd ipphoney && \
    cp /root/dist/requirements.txt .
WORKDIR /opt/ipphoney
RUN pip3 install --break-system-packages pyinstaller
RUN pip3 install --break-system-packages -r requirements.txt
RUN pyinstaller honeypot.py \
		--hidden-import output_plugins \
		--hidden-import output_plugins.jsonlog
RUN ls /opt/ipphoney -alR
#
FROM alpine:3.23
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/ipphoney/dist/* /opt/ipphoney/
COPY --from=builder /opt/ipphoney/ipphoney/data/responses/ /opt/ipphoney/responses/
COPY dist/honeypot.cfg /opt/ipphoney/etc/
#
# Start ipphoney
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/ipphoney/
CMD ["./honeypot"]
