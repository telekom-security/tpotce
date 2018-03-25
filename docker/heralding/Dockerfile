FROM alpine
  
# Include dist
ADD dist/ /root/dist/

# Install packages
RUN apk -U upgrade && \
    apk add bash \
            build-base \
            git \
            libffi-dev \
            libressl-dev \
            postgresql-dev \
            python3 \
            python3-dev \
            py-virtualenv && \
    pip3 install --upgrade pip && \

# Setup heralding
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/johnnykv/heralding && \
    cd heralding && \
    mv /root/dist/heralding.yml /opt/heralding/ && \
    pip3 install -r requirements.txt && \
    pip3 install heralding && \

# Setup user, groups and configs
    addgroup -g 2000 heralding && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 heralding && \
    mkdir -p /var/log/heralding/ && \

# Clean up
    apk del --purge \
            build-base \
            git \
            libffi-dev \
            libressl-dev \
            postgresql-dev \
            python3-dev \
            py-virtualenv && \
    apk add libzmq && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start elasticpot
WORKDIR /opt/heralding/
CMD ["heralding","-l","/var/log/heralding/heralding.log"]
