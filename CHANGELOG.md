# Release Notes / Changelog
T-Pot 22.04.0 is probably the most feature rich release ever provided with long awaited (wanted!) features readily available after installation. 

## New Features
* **Distributed** Installation with **HIVE** and **HIVE_SENSOR**
* **ARM64** support for all provided Docker images
* **GeoIP Attack Map** visualizing Live Attacks on a dedicated webpage
* **Kibana Live Attack Map** visualizing Live Attacks from different **HIVE_SENSORS**
* **Blackhole** is a script trying to avoid mass scanner detection 
* **Elasticvue** a web front end for browsing and interacting with an Elastic Search cluster
* **Ddospot** a honeypot for tracking and monitoring UDP-based Distributed Denial of Service (DDoS) attacks
* **Endlessh** is a SSH tarpit that very slowly sends an endless, random SSH banner
* **HellPot** is an endless honeypot based on Heffalump that sends unruly HTTP bots to hell
* **qHoneypots** 25 honeypots in a single container for monitoring network traffic, bots activities, and username \ password credentials
* **Redishoneypot** is a honeypot mimicking some of the Redis' functions
* **SentryPeer** a dedicated SIP honeypot
* **Index Lifecycle Management** for Elasticseach indices is now being used

## Upgrades
* **Debian 11.x** is now being used for the T-Pot ISO images and required for post installs
* **Elastic Stack 8.x** is now provided as Docker images

## Updates
* **Honeypots** and **tools** were updated to their latest masters and releases
* Updates will be provided continuously through Docker Images updates 

## Breaking Changes
* For security reasons all Py2.x honeypots with the need of PyPi packages have been removed: **HoneyPy**, **HoneySAP** and **RDPY**
* If you are upgrading from a previous version of T-Pot (20.06.x) you need to import the new Kibana objects or some of the functionality will be broken or will be unavailabe
* **Cyberchef** is now part of the Nginx Docker image, no longer as individual image
* **ElasticSearch Head** is superseded by **Elasticvue** and part the Nginx Docker image
* **Heimdall** is no longer supported and superseded with a new Bento based landing page
* **Elasticsearch Curator** is no longer supprted and superseded with **Index Lifecycle Policies** available through Kibana.

# Thanks & Credits
* @ghenry, for some fun late night debugging and of course SentryPeer!
* @giga-a, for adding much appreciated features (i.e. JSON logging, 
X-Forwarded-For, etc.) and of course qHoneypots! 
* @sp3t3rs, @trixam, for their backend and ews support!
* @tadashi-oya, for spotting some errors and propose fixes!
* @tmariuss, @shaderecker for their cloud contributions!
* @vorband, for much appreciated and helpful insights regarding the GeoIP Attack Map!
* @yunginnanet, on not giving up on squashing a bug and of course Hellpot!

... and many others from the T-Pot community by opening valued issues and discussions, suggesting ideas and thus helping to improve T-Pot!