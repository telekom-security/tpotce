FROM alpine:3.12
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
    git clone https://gitlab.com/bontchev/elasticpot.git/ && \
    cd elasticpot && \
    git checkout d12649730d819bd78ea622361b6c65120173ad45 && \
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
    rm -rf /var/cache/apk/*
#
# Start elasticpot
STOPSIGNAL SIGINT
USER elasticpot:elasticpot
WORKDIR /opt/elasticpot/
CMD ["/usr/bin/python3","elasticpot.py"]
