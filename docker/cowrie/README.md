[![](https://images.microbadger.com/badges/version/dtagdevsec/cowrie:1903.svg)](https://microbadger.com/images/dtagdevsec/cowrie:1903 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/cowrie:1903.svg)](https://microbadger.com/images/dtagdevsec/cowrie:1903 "Get your own image badge on microbadger.com")

# cowrie

[cowrie](http://www.micheloosterhof.com/cowrie/) is an extended fork of the medium interaction honeypot [kippo](https://github.com/desaster/kippo).

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized cowrie and will be used to setup the docker image.  

The `docker-compose.yml` contains the necessary settings to test cowrie using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# Cowrie Dashboard

![Cowrie Dashboard](doc/dashboard.png)
