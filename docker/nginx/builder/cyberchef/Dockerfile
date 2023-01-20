#FROM node:17.9.0-alpine3.15 as builder
FROM node:18-alpine3.15 as builder
#
# Prep and build Cyberchef
ENV CY_VER=v9.55.0
RUN apk -U --no-cache add build-base git python3 && \
    chown -R node:node /srv && \
    npm install -g grunt-cli
WORKDIR /srv
USER node
RUN git clone https://github.com/gchq/cyberchef -b $CY_VER . && \
    NODE_OPTIONS=--max_old_space_size=2048 && \
    npm install && \
    grunt prod && \
    cd build/prod && \
    rm CyberChef_$CY_VER.zip && \
    tar cvfz cyberchef.tgz *
#    
FROM scratch AS exporter
COPY --from=builder /srv/build/prod/cyberchef.tgz /
