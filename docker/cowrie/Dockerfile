FROM alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Get and install dependencies & packages
RUN apk -U upgrade && \
    apk add git procps py-pip mpfr-dev openssl-dev mpc1-dev libffi-dev build-base python python-dev py-mysqldb py-requests py-setuptools gmp-dev && \

# Setup user
    addgroup -g 2000 cowrie && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 cowrie && \

# Install cowrie from git
    git clone https://github.com/micheloosterhof/cowrie.git /home/cowrie/cowrie/ && \
    cd /home/cowrie/cowrie && \
    pip install --no-cache-dir --upgrade cffi && \
    pip install --no-cache-dir -U -r requirements.txt && \

# Setup user, groups and configs
    cp /root/dist/cowrie.cfg /home/cowrie/cowrie/cowrie.cfg && \
    cp /root/dist/userdb.txt /home/cowrie/cowrie/data/userdb.txt && \
    chown cowrie:cowrie -R /home/cowrie/* && \

# Clean up
    rm -rf /root/* && \
    apk del git py-pip mpfr-dev mpc1-dev libffi-dev build-base py-mysqldb gmp-dev python-dev && \
    rm -rf /var/cache/apk/*

# Start cowrie
ENV PYTHONPATH /home/cowrie/cowrie
WORKDIR /home/cowrie/cowrie
USER cowrie
CMD ["/usr/bin/twistd", "--nodaemon", "-y", "cowrie.tac", "--pidfile", "var/run/cowrie.pid", "cowrie"]
