FROM alpine:latest
#
# Include dist
ADD dist/ /root/dist/
#
# Get and install dependencies & packages
RUN sed -i 's/dl-cdn/dl-2/g' /etc/apk/repositories && \
    apk -U --no-cache add \
      git \
      nginx \
      nginx-mod-http-headers-more \
      php7 \
      php7-cgi \
      php7-ctype \
      php7-fileinfo \
      php7-fpm \
      php7-json \
      php7-mbstring \
      php7-openssl \
      php7-pdo \
      php7-pdo_pgsql \
      php7-pdo_sqlite \
      php7-session \
      php7-sqlite3 \
      php7-tokenizer \
      php7-xml \
      php7-zip && \
#
# Clone and setup Heimdall, Nginx
    git clone https://github.com/linuxserver/heimdall && \
    cp -R heimdall/. /var/lib/nginx/html && \
    rm -rf heimdall && \
    cd /var/lib/nginx/html && \
    cp .env.example .env && \
    php artisan key:generate && \
#
## Add previously configured content
    mkdir -p /var/lib/nginx/html/storage/app/public/backgrounds/ && \
    cp /root/dist/app/bg1.jpg /var/lib/nginx/html/public/img/bg1.jpg && \
    cp /root/dist/app/t-pot.png /var/lib/nginx/html/public/img/heimdall-icon-small.png && \
    cp /root/dist/app/app.sqlite /var/lib/nginx/html/database/app.sqlite && \
    cp /root/dist/app/cyberchef.png /var/lib/nginx/html/storage/app/public/icons/ZotKKZA2QKplZhdoF3WLx4UdKKhLFamf3lSMcLkr.png && \
    cp /root/dist/app/eshead.png /var/lib/nginx/html/storage/app/public/icons/77KqFv4YIshXUDLDoOvZ1NUbsKDtsMAjJvg4sYqN.png && \
    cp /root/dist/app/tsec.png /var/lib/nginx/html/storage/app/public/icons/RHwXCfCeGNDdhYgzlShL9o4NBFL2LHZWajgyeL0a.png && \
    cp /root/dist/app/spiderfoot.png /var/lib/nginx/html/storage/app/public/icons/s7uPe1frJqjv76oI6SNqNbWUsgU1GHYqRALMlwYb.png && \
    cp /root/dist/html/*.html /var/lib/nginx/html/public/ && \
    cp /root/dist/html/favicon.ico /var/lib/nginx/html/public/favicon-16x16.png && \
    cp /root/dist/html/favicon.ico /var/lib/nginx/html/public/favicon-32x32.png && \
    cp /root/dist/html/favicon.ico /var/lib/nginx/html/public/favicon-96x96.png && \
    cp /root/dist/html/favicon.ico /var/lib/nginx/html/public/favicon.ico && \
#
## Change ownership, permissions
    chown root:www-data -R /var/lib/nginx/html && \
    chmod 775 -R /var/lib/nginx/html/storage && \
    chmod 775 -R /var/lib/nginx/html/database && \
    sed -i "s/user = nobody/user = nginx/g" /etc/php7/php-fpm.d/www.conf && \
    sed -i "s/group = nobody/group = nginx/g" /etc/php7/php-fpm.d/www.conf && \
    sed -i "s#;upload_tmp_dir =#upload_tmp_dir = /var/lib/nginx/tmp#g" /etc/php7/php.ini && \
    sed -i "s/9000/64304/g" /etc/php7/php-fpm.d/www.conf && \
    sed -i "s/APP_NAME=Heimdall/APP_NAME=T-Pot/g" /var/lib/nginx/html/.env && \
## Add Nginx / T-Pot specific configs
    rm -rf /etc/nginx/conf.d/* /usr/share/nginx/html/* && \
    cp /root/dist/conf/nginx.conf /etc/nginx/ && \
    cp -R /root/dist/conf/ssl /etc/nginx/ && \
    cp /root/dist/conf/tpotweb.conf /etc/nginx/conf.d/ && \
    cp /root/dist/start.sh / && \
## Pack database for first time usage
    cd /var/lib/nginx && \
    tar cvfz first.tgz /var/lib/nginx/html/database /var/lib/nginx/html/storage && \
#
# Clean up
    apk del --purge \
      git && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Start nginx
CMD /start.sh && php-fpm7 && exec nginx -g 'daemon off;'
