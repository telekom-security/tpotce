FROM node:10.24.1-alpine3.11 as builder
#
# Prep and build Cyberchef 
RUN apk -U --no-cache add git && \
    chown -R node:node /srv && \
    npm install -g grunt-cli
WORKDIR /srv
USER node
RUN git clone https://github.com/gchq/cyberchef -b v9.32.3 . && \
    NODE_OPTIONS=--max_old_space_size=2048 && \
    npm install && \
    grunt prod && \
    cd build/prod && \
    rm CyberChef_v9.32.3.zip && \
    tar cvfz cyberchef.tgz *
#    
FROM scratch AS exporter
COPY --from=builder /srv/build/prod/cyberchef.tgz /
