[![](https://images.microbadger.com/badges/version/dtagdevsec/mailoney:1903.svg)](https://microbadger.com/images/dtagdevsec/mailoney:1903 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/mailoney:1903.svg)](https://microbadger.com/images/dtagdevsec/mailoney:1903 "Get your own image badge on microbadger.com")

# mailoney

[mailoney](https://github.com/awhitehatter/mailoney) is a SMTP Honeypot.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized mailoney and will be used to setup the docker image.

The `docker-compose.yml` contains the necessary settings to test mailoney using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# Mailoney Dashboard

![Mailoney Dashboard](doc/dashboard.png)
