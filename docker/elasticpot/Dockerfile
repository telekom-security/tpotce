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
		py3-tz \
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
	git checkout 57fdc0e4ed259c7438c581a94feba63833eacf0e && \
    cp /root/dist/requirements.txt .
WORKDIR /opt/elasticpot
RUN pip3 install --break-system-packages pyinstaller
RUN pip3 install --break-system-packages -r requirements.txt
RUN pyinstaller --name elasticpot honeypot.py \
		--hidden-import output_plugins \
		--hidden-import output_plugins.jsonlog
#
FROM alpine:3.23
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/elasticpot/dist/ /opt/
COPY --from=builder /opt/elasticpot/elasticpot/data/responses/ /opt/elasticpot/responses/
COPY --from=builder /root/dist/honeypot.cfg /opt/elasticpot/etc/
#
# Start elasticpot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/elasticpot/
CMD ["./elasticpot"]
