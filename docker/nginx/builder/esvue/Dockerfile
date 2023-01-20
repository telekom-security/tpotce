FROM node:14.18-alpine AS builder
#
# Prep and build Elasticvue 
RUN apk -U --no-cache add git && \
    git clone https://github.com/cars10/elasticvue /opt/src && \
# We need to adjust consts.js so the user has connection suggestion for reverse proxied ES
    sed -i "s#export const DEFAULT_HOST = 'http://localhost:9200'#export const DEFAULT_HOST = window.location.origin + '/es'#g" /opt/src/src/consts.js && \
    sed -i 's#href="/images/logo/favicon.ico"#href="images/logo/favicon.ico"#g' /opt/src/public/index.html && \
    mkdir /opt/app && \
    cd /opt/app && \
    cp /opt/src/package.json . && \
    cp /opt/src/yarn.lock . && \
    yarn install --ignore-optional && \
    cp -R /opt/src/* . && \
# We need to set this ENV so we can run Elasticvue in its own location rather than /
    VUE_APP_PUBLIC_PATH=/elasticvue/ yarn build && \
    cd dist && \
    tar cvfz esvue.tgz *
#    
FROM scratch AS exporter
COPY --from=builder /opt/app/dist/esvue.tgz /
