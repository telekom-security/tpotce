FROM alpine:3.20 AS builder
#
# Install packages
RUN apk --no-cache -U add build-base \
		git \
		libffi \
		libffi-dev \
		openssl \
		openssl-dev \
		py3-cryptography \
		py3-pip \
		python3 \
		python3-dev && \
#
# Get and install packages
    mkdir -p /opt/ && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/ciscoasa_honeypot && \
    cd ciscoasa_honeypot && \
    sed -i "s/git+git/git+https/g" requirements.txt && \
    pip3 install --break-system-packages pyinstaller && \
    pip3 install --break-system-packages --no-cache-dir -r requirements.txt
WORKDIR /opt/ciscoasa_honeypot
RUN pyinstaller asa_server.py --add-data "./asa:./asa"
#
FROM alpine:3.20
COPY --from=builder /opt/ciscoasa_honeypot/dist/ /opt/
#
# Start ciscoasa
STOPSIGNAL SIGINT
WORKDIR /opt/asa_server/
USER 2000:2000
CMD ./asa_server --ike-port 5000 --enable_ssl --port 8443 --verbose >> /var/log/ciscoasa/ciscoasa.log 2>&1
