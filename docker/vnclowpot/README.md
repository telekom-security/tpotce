[![](https://images.microbadger.com/badges/version/dtagdevsec/vnclowpot:1710.svg)](https://microbadger.com/images/dtagdevsec/vnclowpot:1710 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/vnclowpot:1710.svg)](https://microbadger.com/images/dtagdevsec/vnclowpot:1710 "Get your own image badge on microbadger.com")

# vnclowpot

[vnclowpot](https://github.com/magisterquis/vnclowpot) is a low-interaction VNC honeypot with a static challenge.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized vnclowpot and will be used to setup the docker image.

The `docker-compose.yml` contains the necessary settings to test vnclowpot using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# vnclowpot Dashboard

![vnclowpot Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/docker/vnclowpot/doc/dashboard.png)
