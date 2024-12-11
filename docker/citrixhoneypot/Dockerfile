FROM alpine:3.20 AS builder
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
        build-base \
		git \
		openssl \
		py3-pip \
		python3 && \
    pip3 install --break-system-packages --no-cache-dir \
        pyinstaller \
        python-json-logger
#
# Install CitrixHoneypot from GitHub
RUN git clone https://github.com/t3chn0m4g3/CitrixHoneypot /opt/citrixhoneypot && \
    cd /opt/citrixhoneypot && \
    git checkout dee32447033a0296d053e8f881bf190f9dd7ad44 && \
    mkdir -p /opt/citrixhoneypot/logs /opt/citrixhoneypot/ssl && \
    openssl req \
          -nodes \
          -x509 \
          -newkey rsa:2048 \
          -keyout "/opt/citrixhoneypot/ssl/key.pem" \
          -out "/opt/citrixhoneypot/ssl/cert.pem" \
          -days 365 \
          -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' && \
    chown 2000:2000 -R ssl/
#
WORKDIR /opt/citrixhoneypot
RUN pyinstaller CitrixHoneypot.py
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/citrixhoneypot/dist/CitrixHoneypot/ /opt/citrixhoneypot
COPY --from=builder /opt/citrixhoneypot/ssl /opt/citrixhoneypot/ssl
COPY --from=builder /opt/citrixhoneypot/responses/ /opt/citrixhoneypot/responses
#
# Set workdir and start citrixhoneypot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/citrixhoneypot/
CMD nohup ./CitrixHoneypot
