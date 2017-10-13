FROM alpine 
MAINTAINER MO 

# Install packages
RUN apk -U upgrade && \
    apk add autoconf automake bash build-base git libtool procps py-pip python python-dev && \

# Install libemu    
    git clone https://github.com/buffer/libemu /root/libemu/ && \
    cd /root/libemu/ && \
    autoreconf -vi && \
    ./configure && \
    make && \
    make install && \

# Install libemu python wrapper
    pip install pylibemu && \ 

# Install mailoney from git
    git clone https://github.com/awhitehatter/mailoney /opt/mailoney && \

# Setup user, groups and configs
    addgroup -g 2000 mailoney && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 mailoney && \
    chown -R mailoney:mailoney /opt/mailoney && \

# Clean up
    apk del autoconf automake build-base git py-pip python-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Set workdir and start glastopf
USER mailoney
WORKDIR /opt/mailoney/
CMD ["/usr/bin/python","mailoney.py","-i","0.0.0.0","-p","2525","-s","mailserver","-t","schizo_open_relay"]
