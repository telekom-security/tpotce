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
    git clone https://github.com/mushorg/conpot /opt/conpot_guardian_ast/ && \
    cd /opt/conpot_guardian_ast/ && \
    git checkout d97a68a054e4fe42ff90293188a5702ce8ab09a3 && \
    cp /root/dist/requirements.txt /opt/conpot_guardian_ast/ && \
    python setup.py install && \
    cd / && \
    rm -rf /opt/conpot_guardian_ast /tmp/* /var/tmp/* && \

# Setup user, groups and configs
    addgroup -g 2000 conpot_guardian_ast && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 conpot_guardian_ast && \
    mkdir -p /etc/conpot_guardian_ast /var/log/conpot_guardian_ast && \
    mv /root/dist/conpot.cfg /etc/conpot_guardian_ast/conpot_guardian_ast.cfg && \
    mv /root/dist/guardian_ast/template.xml /usr/lib/python2.7/site-packages/Conpot-0.5.1-py2.7.egg/conpot_guardian_ast/templates/guardian_ast/ && \

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
CMD ["/usr/bin/conpot", "--template", "guardian_ast", "--logfile", "/var/log/conpot_guardian_ast/conpot_guardian_ast.log", "--config", "/etc/conpot_guardian_ast/conpot_guardian_ast.cfg"]
