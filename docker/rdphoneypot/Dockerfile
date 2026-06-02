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
		py3-asn1 \
		py3-cryptography \
		py3-geoip2 \
		py3-maxminddb \
		py3-openssl \
		py3-pip \
		py3-requests \
		py3-rsa \
		py3-service_identity \
		py3-setuptools \
		py3-twisted \
		py3-tz \
		py3-wheel \
		python3 \
		python3-dev && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://gitlab.com/bontchev/rdphoneypot.git/ && \
	cd rdphoneypot && \
	git checkout e5cf78e0c95eafe048f897696c3a7c0ec2eeb62e
WORKDIR /opt/rdphoneypot
RUN pip3 install --break-system-packages pyinstaller
RUN pyinstaller --name rdphoneypot honeypot.py \
		--hidden-import output_plugins \
		--hidden-import output_plugins.jsonlog
#
FROM alpine:3.23
RUN apk --no-cache -U upgrade && \
    mkdir -p /opt/rdphoneypot/cert /opt/rdphoneypot/log
COPY --from=builder /opt/rdphoneypot/dist/ /opt/
COPY --from=builder /opt/rdphoneypot/rdphoneypot/data/responses/ /opt/rdphoneypot/responses/
COPY --from=builder /root/dist/honeypot.cfg /opt/rdphoneypot/etc/
RUN chown -R 2000:2000 /opt/rdphoneypot
#
# Start rdphoneypot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/rdphoneypot/
CMD ["./rdphoneypot"]
