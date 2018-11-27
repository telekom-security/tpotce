[![](https://images.microbadger.com/badges/version/dtagdevsec/p0f:1804.svg)](https://microbadger.com/images/dtagdevsec/p0f:1804 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/p0f:1804.svg)](https://microbadger.com/images/dtagdevsec/p0f:1804 "Get your own image badge on microbadger.com")

# p0f

[p0f](http://lcamtuf.coredump.cx/p0f3/) P0f is a tool that utilizes an array of sophisticated, purely passive traffic fingerprinting mechanisms to identify the players behind any incidental TCP/IP communications (often as little as a single normal SYN) without interfering in any way.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized p0f and will be used to setup the docker image.

The `docker-compose.yml` contains the necessary settings to test p0f using `docker-compose`. This will ensure to start the docker container with the appropriate permissions and port mappings.
