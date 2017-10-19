# dockerized portainer (ui-for-docker)

[portainer](http://portainer.io/) Portainer allows you to manage your Docker containers, images, volumes, networks and more ! It is compatible with the standalone Docker engine and with Docker Swarm.

This repository contains the necessary files to create a *dockerized* version of portainer.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized portainer and will be used to setup the docker image.  

Using systemd, copy the `systemd/ui-for-docker.service` to `/etc/systemd/system/ui-for-docker.service` and start using

```
systemctl enable ui-for-docker
systemctl start ui-for-docker
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

# Portainer Dashboard

![Portainer Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/ui-for-docker/master/doc/dashboard1.png)
![Portainer Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/ui-for-docker/master/doc/dashboard2.png)
![Portainer Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/ui-for-docker/master/doc/dashboard3.png)
![Portainer Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/ui-for-docker/master/doc/dashboard4.png)
![Portainer Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/ui-for-docker/master/doc/dashboard5.png)
