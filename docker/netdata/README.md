[![](https://images.microbadger.com/badges/version/dtagdevsec/netdata:1710.svg)](https://microbadger.com/images/dtagdevsec/netdata:1710 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/netdata:1710.svg)](https://microbadger.com/images/dtagdevsec/netdata:1710 "Get your own image badge on microbadger.com")

# netdata

[netdata](http://my-netdata.io/) is a system for distributed real-time performance and health monitoring. It provides unparalleled insights, in real-time, of everything happening on the system it runs (including applications such as web, or database servers), using modern interactive web dashboards. netdata is fast and efficient, designed to permanently run on all systems (physical & virtual servers, containers, IoT devices), without disrupting their core function.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized netdata and will be used to setup the docker image.  

The `docker-compose.yml` contains the necessary settings to test netdata using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# Netdata Dashboard

![Netdata Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/docker/netdata/doc/dashboard.png)
