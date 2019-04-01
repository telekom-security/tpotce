[![](https://images.microbadger.com/badges/version/dtagdevsec/medpot:1903.svg)](https://microbadger.com/images/dtagdevsec/medpot:1903 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/medpot:1903.svg)](https://microbadger.com/images/dtagdevsec/medpot:1903 "Get your own image badge on microbadger.com")

# Medpot

[Medpot](https://github.com/schmalle/medpot) is a SMTP Honeypot.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized Medpot and will be used to setup the docker image.

The `docker-compose.yml` contains the necessary settings to test Medpot using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# Medpot Dashboard

![Medpot Dashboard](doc/dashboard.png)
