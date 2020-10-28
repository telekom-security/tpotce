FROM alpine:3.12.1
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
             build-base \
	     ca-certificates \
             git \
	     libcap \
             libffi-dev \
	     openssl \
             openssl-dev \
	     postgresql-dev \
             py3-mysqlclient \
             py3-requests \
	     py3-pip \
             python3 \
             python3-dev && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://gitlab.com/bontchev/ipphoney.git/ && \
    cd ipphoney && \
    git checkout 7ab1cac437baba17cb2cd25d5bb1400327e1bb79 && \
    pip3 install -r requirements.txt && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.8 && \
#
# Setup user, groups and configs
    addgroup -g 2000 ipphoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 ipphoney && \
    mv /root/dist/honeypot.cfg /opt/ipphoney/etc/ && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    libffi-dev \
		    openssl-dev \
		    postgresql-dev \
		    python3-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start ipphoney
STOPSIGNAL SIGINT
USER ipphoney:ipphoney
WORKDIR /opt/ipphoney/
CMD ["/usr/bin/python3","ipphoney.py"]
