[![](https://images.microbadger.com/badges/version/ghcr.io/telekom-security/glastopf:1903.svg)](https://microbadger.com/images/ghcr.io/telekom-security/glastopf:1903 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/ghcr.io/telekom-security/glastopf:1903.svg)](https://microbadger.com/images/ghcr.io/telekom-security/glastopf:1903 "Get your own image badge on microbadger.com")

# glastopf (deprecated)

[glastopf](https://github.com/mushorg/glastopf) is a python web application honeypot.

This dockerized version is part of the **[T-Pot community honeypot](http://telekom-security.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized glastopf and will be used to setup the docker image.

The `docker-compose.yml` contains the necessary settings to test glastopf using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.  

# Glastopf Dashboard

![Glastopf Dashboard](doc/dashboard.png)
