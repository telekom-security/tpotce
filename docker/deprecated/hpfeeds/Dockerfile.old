### This is only for testing purposes, do NOT use for production
FROM alpine:latest
#
ADD dist/ /root/dist/
#
# Install packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
               build-base \
               coreutils \
               git \
               libffi \
               libffi-dev \
               py-gevent \
               py-pip \
               python \
               python-dev \
               sqlite && \
#
# Install php sandbox from git
    git clone --depth=1 https://github.com/rep/hpfeeds /opt/hpfeeds && \
    cd /opt/hpfeeds/broker && \
    sed -i -e '87d;88d' database.py && \
    cp /root/dist/adduser.sql . && \
    cd /opt/hpfeeds/broker && timeout 5 python broker.py || : && \
    sqlite3 db.sqlite3 < adduser.sql && \ 
#    
    #python setup.py build && \
    #python setup.py install && \
#
# Clean up
    apk del --purge autoconf \
                    build-base \
                    coreutils \
                    libffi-dev \
                    python-dev && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start glastopf
WORKDIR /opt/hpfeeds/broker
CMD python broker.py
