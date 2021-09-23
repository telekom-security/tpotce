FROM alpine:3.11
#
# Include dist
ADD dist/ /root/dist/
#
# Get and install dependencies & packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U add \
            build-base \
            git \
            libffi-dev \
            openssl \
            openssl-dev \
            python \
            python-dev \
            py-pip \
            py-setuptools && \
#
# Setup user
    addgroup -g 2000 rdpy && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 rdpy && \
#
# Install deps 
    pip install --no-cache-dir --upgrade cffi && \
    pip install --no-cache-dir \
                hpfeeds \
                twisted \
                pyopenssl \
                qt4reactor \
                service_identity \
                rsa==4.5 \		
                pyasn1 && \
#
# Install rdpy from git
    mkdir -p /opt && \
    cd /opt && \
    git clone https://github.com/t3chn0m4g3/rdpy && \
    cd rdpy && \
    git checkout 1d2a4132aefe0637d09cac1a6ab83ec5391f40ca && \
    python setup.py install && \
#
# Setup user, groups and configs
    cp /root/dist/* /opt/rdpy/ && \
    chown rdpy:rdpy -R /opt/rdpy/* && \
    mkdir -p /var/log/rdpy && \
#
# Clean up
    rm -rf /root/* && \
    apk del --purge build-base \
                    git \
                    libffi-dev \
                    openssl-dev \
                    python-dev \
                    py-pip && \ 
    rm -rf /var/cache/apk/*
#
# Start rdpy
USER rdpy:rdpy
CMD exec /usr/bin/python2 -i /usr/bin/rdpy-rdphoneypot.py /opt/rdpy/$(shuf -i 1-3 -n 1) >> /var/log/rdpy/rdpy.log
