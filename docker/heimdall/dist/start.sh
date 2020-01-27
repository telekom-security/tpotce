#!/bin/ash
if [ "$(ls /var/lib/nginx/html/database)" = "" ] && [ "$HEIMDALL_PERSIST" = "YES" ];
  then
    tar xvfz /var/lib/nginx/first.tgz -C /
fi
if [ "$HEIMDALL_PERSIST" = "YES" ];
  then
    chmod 770 -R /var/lib/nginx/html/database /var/lib/nginx/html/storage
    chown root:www-data -R /var/lib/nginx/html/database /var/lib/nginx/html/storage
fi
