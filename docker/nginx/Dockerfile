ARG ALPINE_VERSION=3.23

FROM alpine:${ALPINE_VERSION} AS assets
#
# Include dist and prepare final assets
COPY dist/ /work/dist/
RUN mkdir -p /out/var/lib/nginx/html /out/etc/nginx/conf.d && \
    cd /work/dist/html/esvue && \
    sha256sum -c esvue.tgz.sha256 && \
    cd /work/dist/html/cyberchef && \
    sha256sum -c cyberchef.tgz.sha256 && \
    cp -R /work/dist/html/* /out/var/lib/nginx/html/ && \
    cd /out/var/lib/nginx/html/esvue && \
    tar xvfz esvue.tgz && \
    rm -f esvue.tgz && \
    cd /out/var/lib/nginx/html/cyberchef && \
    tar xvfz cyberchef.tgz && \
    rm -f cyberchef.tgz && \
    find /out/var/lib/nginx/html -type d -exec chmod 755 {} \; && \
    find /out/var/lib/nginx/html -type f -exec chmod 644 {} \; && \
    cp /work/dist/conf/nginx.conf /out/etc/nginx/ && \
    cp -R /work/dist/conf/ssl /out/etc/nginx/ && \
    cp /work/dist/conf/tpotweb.conf /out/etc/nginx/conf.d/ && \
    cp /work/dist/conf/lsweb.conf /out/etc/nginx/conf.d/

FROM alpine:${ALPINE_VERSION}
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
      nginx \
      nginx-mod-http-brotli \
      nginx-mod-http-headers-more \
      nginx-mod-http-lua && \
    rm -rf /etc/nginx/conf.d/* /usr/share/nginx/html/*
#
## Add prepared web assets and Nginx / T-Pot specific configs
COPY --from=assets --chown=root:www-data /out/var/lib/nginx/html/ /var/lib/nginx/html/
COPY --from=assets /out/etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=assets /out/etc/nginx/ssl/ /etc/nginx/ssl/
COPY --from=assets /out/etc/nginx/conf.d/tpotweb.conf /etc/nginx/conf.d/tpotweb.conf
COPY --from=assets /out/etc/nginx/conf.d/lsweb.conf /etc/nginx/conf.d/lsweb.conf
#
# Start nginx
CMD ["nginx", "-g", "daemon off;"]
