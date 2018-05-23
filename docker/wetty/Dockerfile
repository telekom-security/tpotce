FROM alpine

# Setup env and apt
RUN apk -U --no-cache add \
                build-base \
                git \
                nodejs \
                nodejs-npm \
                openssh-client \
                python && \

# Setup user
    addgroup -g 2000 wetty && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 wetty && \
    #mkdir -p /home/wetty/.ssh && \
    #chmod 755 /home/wetty/.ssh && \
    #chown wetty:wetty /home/wetty/.ssh && \
    #touch /home/wetty/.ssh/known_hosts && \
    #chmod 777 /home/wetty/.ssh/known_hosts && \
    #chown wetty:wetty /home/wetty/.ssh/known_hosts && \

# Get and install packages
    mkdir -p /opt/ && \
    cd /opt/ && \
    npm install https://github.com/t3chn0m4g3/wetty && \

# Clean up
    apk del --purge build-base \
                    git \
                    nodejs-npm \
                    python

# Start elasticsearch-head
WORKDIR /opt
USER wetty:wetty
CMD exec /usr/bin/node /opt/node_modules/wetty/app.js -p 64300 --host 127.0.0.1 --sshhost 127.0.0.1 --sshport 64295 --sshuser $MY_SSHUSER
