FROM alpine
MAINTAINER MS/MO

# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U upgrade && \ 
    apk add bash python3 git && \
    pip3 install --upgrade pip && \
    pip3 install bottle requests configparser datetime && \
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/schmalle/ElasticpotPY.git && \

# Setup user, groups and configs
    addgroup -g 2000 elasticpot && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 elasticpot && \
    mv /root/dist/elasticpot.cfg /opt/ElasticpotPY/ && \
    mkdir /opt/ElasticpotPY/log && \

# Clean up
    apk del git && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start elasticpot
USER elasticpot
WORKDIR /opt/ElasticpotPY/
CMD ["/usr/bin/python3","main.py"]
