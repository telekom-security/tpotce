FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
#
# Get and install dependencies & packages
RUN apk -U --no-cache add \
      nginx \
      nginx-mod-http-brotli \
      nginx-mod-http-headers-more \
      nginx-mod-http-lua && \ 
#
## Setup T-Pot Landing Page, Eleasticvue, Cyberchef 
    cp -R /root/dist/html/* /var/lib/nginx/html/ && \
    cd /var/lib/nginx/html/esvue && \
    tar xvfz esvue.tgz && \
    rm esvue.tgz && \
    cd /var/lib/nginx/html/cyberchef && \
    tar xvfz cyberchef.tgz && \
    rm cyberchef.tgz && \
#
## Change ownership, permissions
    chown root:www-data -R /var/lib/nginx/html && \
    chmod 755 -R /var/lib/nginx/html && \
#
## Add Nginx / T-Pot specific configs
    rm -rf /etc/nginx/conf.d/* /usr/share/nginx/html/* && \
    mkdir -p /etc/nginx/conf.d && \
    cp /root/dist/conf/nginx.conf /etc/nginx/ && \
    cp -R /root/dist/conf/ssl /etc/nginx/ && \
    cp /root/dist/conf/tpotweb.conf /etc/nginx/conf.d/ && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start nginx
CMD nginx -g 'daemon off;'
