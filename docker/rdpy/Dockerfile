FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Get and install dependencies & packages
RUN apk -U upgrade && \
    apk add bash build-base git libffi-dev openssl openssl-dev procps python python-dev py-pip py-setuptools && \
    apk -U add --repository https://dl-cdn.alpinelinux.org/alpine/edge/testing/ \
            py-qt && \

# Setup user
    addgroup -g 2000 rdpy && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 rdpy && \

# Install rdpy from git
    cd /home/rdpy && \
    git clone https://github.com/t3chn0m4g3/rdpy && \
    pip install --no-cache-dir --upgrade cffi && \
    pip install twisted pyopenssl qt4reactor service_identity rsa pyasn1==0.3.4 && \
    cd rdpy && \
    python setup.py install && \

# Setup user, groups and configs
    cp /root/dist/* /home/rdpy/ && \
    chown rdpy:rdpy -R /home/rdpy/* && \
    mkdir -p /var/log/rdpy && \

# Clean up
    rm -rf /root/* && \
    apk del build-base libffi-dev openssl-dev python-dev py-pip py-qt && \
    rm -rf /var/cache/apk/*

# Start rdpy
CMD /usr/bin/python2 -i /usr/bin/rdpy-rdphoneypot.py /home/rdpy/1 /home/rdpy/2 /home/rdpy/3 >> /var/log/rdpy/rdpy.log
