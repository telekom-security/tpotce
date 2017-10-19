FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Setup apt
RUN apk -U add bash \
               build-base \
               file \
               git \
               libev \
               libtool \
               libxslt \
               libxslt-dev \
               mariadb-dev \
               mariadb-client-libs \
               pkgconfig \
               python \
               python-dev \
               py-cffi && \

# Setup ConPot
    git clone https://github.com/mushorg/conpot /opt/conpot/ && \
    cd /opt/conpot/ && \
    git checkout d97a68a054e4fe42ff90293188a5702ce8ab09a3 && \
    cp /root/dist/requirements.txt /opt/conpot/ && \
    python setup.py install && \
    cd / && \
    rm -rf /opt/conpot /tmp/* /var/tmp/* && \

# Setup user, groups and configs
    addgroup -g 2000 conpot && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 conpot && \
    mkdir -p /etc/conpot /var/log/conpot && \
    mv /root/dist/conpot.cfg /etc/conpot/conpot.cfg && \
    mv /root/dist/kamstrup_382/template.xml /usr/lib/python2.7/site-packages/Conpot-0.5.1-py2.7.egg/conpot/templates/kamstrup_382/ && \

# Clean up
    apk del build-base \
            file \
            git \
            libev \
            libtool \
            libxslt-dev \
            mariadb-dev \
            pkgconfig \
            python-dev \
            py-cffi && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Run supervisor upon container start
CMD ["/usr/bin/conpot", "--template", "kamstrup_382", "--logfile", "/var/log/conpot/conpot.log", "--config", "/etc/conpot/conpot.cfg"]
