[![](https://images.microbadger.com/badges/version/dtagdevsec/rdpy:1903.svg)](https://microbadger.com/images/dtagdevsec/rdpy:1903 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/rdpy:1903.svg)](https://microbadger.com/images/dtagdevsec/rdpy:1903 "Get your own image badge on microbadger.com")

# rdpy

[rdpy](https://github.com/citronneur/rdpy) RDPY is a pure Python implementation of the Microsoft RDP (Remote Desktop Protocol) protocol (client and server side). RDPY is built over the event driven network engine Twisted. RDPY support standard RDP security layer, RDP over SSL and NLA authentication (through ntlmv2 authentication protocol).

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized rdpy and will be used to setup the docker image.  

The `docker-compose.yml` contains the necessary settings to test rdpy using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.

# RDPY Dashboard

![RDPY Dashboard](doc/dashboard.png)
