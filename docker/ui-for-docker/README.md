[![](https://images.microbadger.com/badges/version/dtagdevsec/ui-for-docker:1710.svg)](https://microbadger.com/images/dtagdevsec/ui-for-docker:1710 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/ui-for-docker:1710.svg)](https://microbadger.com/images/dtagdevsec/ui-for-docker:1710 "Get your own image badge on microbadger.com")

# portainer

[portainer](http://portainer.io/) Portainer allows you to manage your Docker containers, images, volumes, networks and more ! It is compatible with the standalone Docker engine and with Docker Swarm.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized portainer and will be used to setup the docker image.  

The `docker-compose.yml` contains the necessary settings to test portainer using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# Portainer UI

![Portainer UI](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/docker/ui-for-docker/doc/dashboard.png)
