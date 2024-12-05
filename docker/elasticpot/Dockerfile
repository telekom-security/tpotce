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
    cp /root/dist/requirements.txt .
WORKDIR /opt/elasticpot
RUN pip3 install --break-system-packages pyinstaller
RUN pip3 install --break-system-packages -r requirements.txt
RUN pyinstaller elasticpot.py \
		--hidden-import output_plugins \
		--hidden-import output_plugins.jsonlog
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/elasticpot/dist/ /opt/
COPY --from=builder /opt/elasticpot/responses/ /opt/elasticpot/responses/
COPY --from=builder /root/dist/honeypot.cfg /opt/elasticpot/etc/
#
# Start elasticpot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/elasticpot/
CMD ["./elasticpot"]
