#!/bin/sh
# Create a self-signed certificate for the TLS ports on the first start, it is
# kept in the mounted cert folder.
if ! [ -f "config/cert/key.pem" ]; then
	openssl req \
	      -nodes \
	      -x509 \
	      -newkey rsa:2048 \
	      -keyout "config/cert/key.pem" \
	      -out "config/cert/cert.pem" \
	      -days 3650 \
	      -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd'
fi
exec ./galah -c config/config.yaml -r config/rules.yaml -o log/galah.json -f config/cache/cache.db
