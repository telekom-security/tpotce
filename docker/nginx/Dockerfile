FROM nginx:alpine
MAINTAINER MO

# Include dist
ADD dist/ /root/dist/

# Get and install dependencies & packages
RUN apk -U upgrade && \
    apk add bash nginx nginx-mod-http-headers-more procps && \

# Setup configs
    rm /etc/nginx/conf.d/* /usr/share/nginx/html/* && \
    cp /root/dist/conf/nginx.conf /etc/nginx/ && \
    cp -R /root/dist/conf/ssl /etc/nginx/ && \
    cp /root/dist/conf/tpotweb.conf /etc/nginx/conf.d/ && \
    cp -R /root/dist/html/ /var/lib/nginx/ && \

# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*

# Start nginx
CMD nginx -g 'daemon off;'
