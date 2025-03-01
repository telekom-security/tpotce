### elasticvue build is currently broken, issue has been opened https://github.com/cars10/elasticvue/issues/215
### in the meantime we are using the older dist, if not resolved we need to find a different solution
FROM node:20-alpine AS builder
#
# Prep and build Elasticvue 
RUN apk -U --no-cache add git && \
    git clone https://github.com/cars10/elasticvue -b v1.1.0 /opt/src && \
# We need to adjust consts.ts so the user has connection suggestion for reverse proxied ES
    sed -i "s#export const DEFAULT_CLUSTER_URI = 'http://localhost:9200'#export const DEFAULT_CLUSTER_URI = window.location.origin + '/es'#g" /opt/src/src/consts.ts && \
    sed -i 's#href="/images/logo/favicon.ico"#href="images/logo/favicon.ico"#g' /opt/src/index.html && \
    mkdir /opt/app && \
    cd /opt/app && \
    cp /opt/src/package.json . && \
    cp /opt/src/yarn.lock . && \
    yarn install && \
    cp -R /opt/src/* . && \
    export VITE_APP_BUILD_MODE=docker && \
    export VITE_APP_PUBLIC_PATH="/elasticvue/" && \
    yarn build && \
    cd dist && \
    tar cvfz esvue.tgz *
#    
FROM scratch AS exporter
COPY --from=builder /opt/app/dist/esvue.tgz /
