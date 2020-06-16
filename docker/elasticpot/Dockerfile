FROM alpine:latest
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
             git \
	     py3-pip \
             python3 && \
    pip3 install --no-cache-dir bottle \
                                configparser \
                                datetime \
                                requests && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone --depth=1 https://github.com/schmalle/ElasticpotPY.git && \
#
# Setup user, groups and configs
    addgroup -g 2000 elasticpot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 elasticpot && \
    mv /root/dist/elasticpot.cfg /opt/ElasticpotPY/ && \
    mkdir /opt/ElasticpotPY/log && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start elasticpot
STOPSIGNAL SIGINT
USER elasticpot:elasticpot
WORKDIR /opt/ElasticpotPY/
CMD ["/usr/bin/python3","main.py"]
