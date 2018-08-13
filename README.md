# T-Pot 18.10

T-Pot 18.10 runs on the latest 18.04 LTS Ubuntu Server Network Installer image, is based on

[docker](https://www.docker.com/), [docker-compose](https://docs.docker.com/compose/)

and includes dockerized versions of the following honeypots

* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticPot),
* [glastopf](http://mushmush.org/),
* [glutton](https://github.com/mushorg/glutton),
* [heralding](https://github.com/johnnykv/heralding),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [rdpy](https://github.com/citronneur/rdpy),
* [snare](http://mushmush.org/),
* [tanner](http://mushmush.org/),
* [vnclowpot](https://github.com/magisterquis/vnclowpot)


Furthermore we use the following tools

* [Cockpit](https://cockpit-project.org/running) for a lightweight, webui for docker, os, real-time performance monitoring and web terminal.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.


# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 6-8 GB RAM and 128 GB free disk space as well as a working internet connection.
2. Download the T-Pot ISO from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](https://sicherheitstacho.eu) and [analyze](#kibana).

# Seeing is believing :bowtie:

[![T-Pot 17.10](https://img.youtube.com/vi/G-_OabDowFU/0.jpg)](https://youtu.be/G-_OabDowFU)


# Table of Contents
- [Changelog](#changelog)
- [Technical Concept](#concept)
- [System Requirements](#requirements)
- [Installation](#installation)
  - [Prebuilt ISO Image](#prebuilt)
  - [Create your own ISO Image](#createiso)
  - [Running in a VM](#vm)
  - [Running on Hardware](#hardware)
  - [Post Install](#postinstall)
  - [First Run](#firstrun)
  - [System Placement](#placement)
- [Options](#options)
  - [SSH and web access](#ssh)
  - [Kibana Dashboard](#kibana)
  - [Tools](#tools)
  - [Maintenance](#maintenance)
  - [Community Data Submission](#submission)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)
- [FAQ](#faq)
- [Contact](#contact)
- [Licenses](#licenses)
- [Credits](#credits)
- [Stay tuned](#staytuned)
- [Fun Fact](#funfact)

<a name="changelog"></a>
# Changelog
- **New honeypots**
	- *Ciscoasa* a low interaction honeypot for the Cisco ASA component capable of detecting CVE-2018-0101, a DoS and remote code execution vulnerability. 
	- *Glutton* (experimental) is the all eating honeypot
	- *Heralding* a credentials catching honeypot.
	- *Snare* is a web application honeypot sensor, is the successor of Glastopf. SNARE has feature parity with Glastopf and allows to convert existing web pages into attack surfaces.
	- *Tanner* is SNARES' "brain". Every event is send from SNARE to TANNER, gets evaluated and TANNER decides how SNARE should respond to the client. This allows us to change the behaviour of many sensors on the fly. We are providing a TANNER instance for your use, but there is nothing stopping you from setting up your own instance.
- **New tools**
	- *Cockpit* is an interactive server admin interface. It is easy to use and very lightweight. Cockpit interacts directly with the operating system from a real Linux session in a browser.
	- *Cyberchef* is the Cyber Swiss Army Knife - a web app for encryption, encoding, compression and data analysis.
	- *grc* (commandline) is yet another colouriser (written in python) for beautifying your logfiles or output of commands
	- *multitail* (commandline) allows you to monitor logfiles and command output in multiple windows in a terminal, colorize, filter and merge.
- **Deprecated tools**
	- *Netdata*, *Portainer* and *WeTTY* were superseded by *Cockpit* which is much more lightweight, perfectly well integrated into Ubuntu 18.04 LTS and of course comes with the same but a more basic feature set. 
- **New Standard Installation**
        - The new standard installation is now running a whopping *14* honeypot instances.
- **T-Pot Universal Installer**
	- The T-Pot installer now also includes the option to install on a existing machine, the T-Pot-Autoinstaller is no longer necessary.
- **Tighten Security**
	- The docker containers are now running mostly with a read-only file system
	- If possible using `setcap` to start daemons without root or dropping privileges
	- Introducing `fail2ban` to ease up on `authorized_keys` requirement which is no longer necessary for `SSH`. Also to further prevent brute-force attacks on `Cockpit` and `NGINX` allowing for faster load times of the WebUI.
- **Iptables exceptions for NFQ based honeypots**
	- In previous versions `iptables`had manually be maintained, now a a script parses `/opt/tpot/etc/tpot.yml` and extracts port information to automatically generate exceptions for ports that should not be forwarded to NFQ.
- **CI**
	- The Kibana UI now uses a magenta theme.
- **ES HEAD**
	- A Java Script now automatically enters the correct FQDN / IP. A manual step is no longer required.
- **ELK STACK**
	- The ELK Stack was updated to the latest 6.x versions.
	- This also means you can now expect the availability of basic *X-Pack-Feaures*, the full feature set however is only available to users with a valid license.
- **Dashboards Makeover**
	- Because Kibana 6.x introduced so much whitespace the dashboards and some of the visualizations needed some overhaul. While it probably needs some getting used to the key was to focus on displaying as much information while not compromising on clarity.  
	- Because of the new honeypots we now have almost **200 Visualizations** pre-configured and compiled to 15 individual **Kibana Dashboards**. Monitor all *honeypot events* locally on your T-Pot installation. Aside from *honeypot events* you can also view *Suricata NSM and NGINX* events for a quick overview of local host events.
- **Honeypot updates and improvements**
	- All honeypots were updated to their latest stable versions.
	- Docker images were mostly overhauled to tighten security even further
	- Some of the honeypot configurations were modified to keep things fresh
- **Update Feature**
	- For the ones who like to live on the bleeding edge of T-Pot development there is now a update script available in `/opt/tpot/update.sh`. 
	- This feature is now in beta and is mostly intended to provide you with the latest development advances without the need of reinstalling T-Pot.

<a name="concept"></a>
# Technical Concept

T-Pot is based on the network installer of Ubuntu Server 18.04.x LTS.
The honeypot daemons as well as other support components being used have been containerized using [docker](http://docker.io).
This allows us to run multiple honeypot daemons on the same network interface while maintaining a small footprint and constrain each honeypot within its own environment.

In T-Pot we combine the dockerized honeypots ...
* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticPot),
* [glastopf](http://mushmush.org/),
* [glutton](https://github.com/mushorg/glutton),
* [heralding](https://github.com/johnnykv/heralding),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [rdpy](https://github.com/citronneur/rdpy),
* [snare](http://mushmush.org/),
* [tanner](http://mushmush.org/),
* [vnclowpot](https://github.com/magisterquis/vnclowpot)

... with the following tools ...
* [Cockpit](https://cockpit-project.org/running) for a lightweight, webui for docker, os, real-time performance monitoring and web terminal.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.

... to give you the best out-of-the-box experience possible and a easy-to-use multi-honeypot appliance. 

![Architecture](doc/architecture.png)

While data within docker containers is volatile we do now ensure a default 30 day persistence of all relevant honeypot and tool data in the well known `/data` folder and sub-folders. The persistence configuration may be adjusted in `/opt/tpot/etc/logrotate/logrotate.conf`. Once a docker container crashes, all other data produced within its environment is erased and a fresh instance is started from the corresponding docker image.<br>

Basically, what happens when the system is booted up is the following:

- start host system
- start all the necessary services (i.e. cockpit, docker, etc.)
- start all docker containers via docker-compose (honeypots, nms, elk, etc.)

Within the T-Pot project, we provide all the tools and documentation necessary to build your own honeypot system and contribute to our [Sicherheitstacho](https://sicherheitstacho.eu).

The source code and configuration files are fully stored in the T-Pot GitHub repository. The docker images are pre-configured for the T-Pot environment. If you want to run the docker images separately, make sure you study the docker-compose configuration (`/opt/tpot/etc/tpot.yml`) and the T-Pot systemd script (`/etc/systemd/system/tpot.service`), as they provide a good starting point for implementing changes.

The individual docker configurations are located in the [docker folder](https://github.com/dtag-dev-sec/tpotce/tree/master/docker).

<a name="requirements"></a>
# System Requirements
Depending on your installation type, whether you install on [real hardware](#hardware) or in a [virtual machine](#vm), make sure your designated T-Pot system meets the following requirements:

##### T-Pot Standard Installation
- Honeypots: ciscoasa, conpot, cowrie, dionaea, elasticpot, heralding, honeytrap, mailoney, rdpy, snare, tanner and vnclowpot
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Sensor Installation
- Honeypots: ciscoasa, conpot, cowrie, dionaea, elasticpot, heralding, honeytrap, mailoney, rdpy, snare, tanner and vnclowpot
- Tools: cockpit

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Industrial Installation
- Honeypots: conpot, rdpy, vnclowpot
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Collector Installation (because sometimes all you want to do is catching credentials)
- Honeypots: heralding
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Experimental Installation
- Honeypots: ciscoasa, conpot, cowrie, dionaea, elasticpot, glutton, heralding, mailoney, rdpy, snare, tanner and vnclowpot
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Legacy Installation (honeypots based on Standard Installation of T-Pot 17.10)
- Honeypots: cowrie, dionaea, elasticpot, glastopf, honeytrap, mailoney, rdpy and vnclowpot
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward and heavily depends on a working, transparent and non-proxied up and running internet connection. Otherwise the installation **will fail!**

Firstly, decide if you want to download our prebuilt installation ISO image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases), [create it yourself](#createiso) ***or*** [post-install on a existing Ubuntu Server 18.04 LTS](#postinstall).

Secondly, decide where you want to let the system run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
We provide an installation ISO image for download (~50MB), which is created using the same [tool](https://github.com/dtag-dev-sec/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) and jump to the [installation](#vm) section.

<a name="createiso"></a>
## Create your own ISO Image
For transparency reasons and to give you the ability to customize your install, we provide you the [ISO Creator](https://github.com/dtag-dev-sec/tpotce) that enables you to create your own ISO installation image.

**Requirements to create the ISO image:**
- Ubuntu 18.04 LTS or newer as host system (others *may* work, but *remain* untested)
- 4GB of free memory  
- 32GB of free storage
- A working internet connection

**How to create the ISO image:**

1. Clone the repository and enter it.
```
git clone https://github.com/dtag-dev-sec/tpotce
cd tpotce
```
2. Invoke the script that builds the ISO image.
The script will download and install dependencies necessary to build the image on the invoking machine. It will further download the ubuntu network installer image (~50MB) which T-Pot is based on.
```
sudo ./makeiso.sh
```
After a successful build, you will find the ISO image `tpot.iso` along with a SHA256 checksum `tpot.sha256` in your directory.

<a name="vm"></a>
## Running in VM
You may want to run T-Pot in a virtualized environment. The virtual system configuration depends on your virtualization provider.

We successfully tested T-Pot with [VirtualBox](https://www.virtualbox.org) and [VMWare](http://www.vmware.com) with just little modifications to the default machine configurations.

It is important to make sure you meet the [system requirements](#requirements) and assign a virtual harddisk and RAM according to the requirements while making sure networking is bridged.

You need to enable promiscuous mode for the network interface for suricata and p0f to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as a primary NIC for T-Pot, please be aware of the fact that not all network interface drivers support all wireless cards. E.g. in VirtualBox, you then have to choose the *"MT SERVER"* model of the NIC.

Lastly, mount the `tpot.iso` ISO to the VM and continue with the installation.<br>

You can now jump [here](#firstrun).

<a name="hardware"></a>
## Running on Hardware
If you decide to run T-Pot on dedicated hardware, just follow these steps:

1. Burn a CD from the ISO image or make a bootable USB stick using the image. <br>
Whereas most CD burning tools allow you to burn from ISO images, the procedure to create a bootable USB stick from an ISO image depends on your system. There are various Windows GUI tools available, e.g. [this tip](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-windows) might help you.<br> On [Linux](http://askubuntu.com/questions/59551/how-to-burn-a-iso-to-a-usb-device) or [MacOS](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-mac-osx) you can use the tool *dd* or create the USB stick with T-Pot's [ISO Creator](https://github.com/dtag-dev-sec).
2. Boot from the USB stick and install.

*Please note*: We will ensure the compatibility with the Intel NUC platform, as we really like the form factor, looks and build quality. Other platforms **remain untested**.

<a name="postinstall"></a>
## Post-Install
In some cases it is necessary to install Ubuntu Server 18.04 LTS on your own:
	- Cloud provider does not offer mounting ISO images.
	- Hardware setup needs special drivers and / or kernels.
	- Within your company you have to setup special policies, software etc.
	- You just like to stay on top of things.

While the T-Pot-Autoinstaller served us perfectly well in the past we decided to include the feature directly into T-Pot and its Universal Installer.

Just follow these steps: 

```
git clone https://github.com/dtag-dev-sec/tpotce
cd tpotce/iso/installer/
./install.sh --type=user
```

The installer will now start and guide you through the install process.

You can also let the installer run automatically if you provide your own `tpot.conf`. A example is available in `tpotce/iso/installer/tpot.conf.dist`.

<a name="firstrun"></a>
## First Run
The installation requires very little interaction, only a locale and keyboard setting have to be answered for the basic linux installation. The system will reboot and please maintain the active internet connection. The T-Pot installer will start and ask you for an installation type, password for the **tsec** user and credentials for a **web user**. Everything else will be configured automatically. All docker images and other componenents will be downloaded. Depending on your network connection and the chosen installation type, the installation may take some time. During our tests (250Mbit down, 40Mbit up), the installation was usually finished within a 15-30 minute timeframe.

Once the installation is finished, the system will automatically reboot and you will be presented with the T-Pot login screen. On the console you may login with:

- user: **[tsec, user you chose during post install method]**
- pass: **password you chose during the installation**

All honeypot services are preconfigured and are starting automatically.

You can login from your browser and access the Admin UI: `https://<your.ip>:64294` or via SSH to access the command line: `ssh -l tsec -p 64295 <your.ip>`

- user: **[tsec, user you chose during post install method]**
- pass: **password you chose during the installation**

You can also login from your browser and access the Web UI: `https://<your.ip>:64297`
- user: **user you chose during the installation**
- pass: **password you chose during the installation**


<a name="placement"></a>
# System Placement
Make sure your system is reachable through the internet. Otherwise it will not capture any attacks, other than the ones from your  internal network! We recommend you put it in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface. However to avoid fingerprinting you can put T-Pot behind a firewall and forward all TCP / UDP traffic in the port range of 1-64000 to T-Pot while allowing access to ports > 64000 only from trusted IPs.

A list of all relevant ports is available as part of the [Technical Concept](#concept)
<br>

Basically, you can forward as many TCP ports as you want, as honeytrap dynamically binds any TCP port that is not covered by the other honeypot daemons.

In case you need external Admin UI access, forward TCP port 64294 to T-Pot, see below.
In case you need external SSH access, forward TCP port 64295 to T-Pot, see below.
In case you need external Web UI access, forward TCP port 64297 to T-Pot, see below.

T-Pot requires outgoing git, http, https connections for updates (Ubuntu, Docker, GitHub, PyPi) and attack submission (ewsposter, hpfeeds). Ports and availability may vary based on your geographical location.

<a name="options"></a>
# Options
The system is designed to run without any interaction or maintenance and automatically contributes to the community.<br>
We know, for some this may not be enough. So here come some ways to further inspect the system and change configuration parameters.

<a name="ssh"></a>
## SSH and web access
By default, the SSH daemon allows access on **tcp/64295** with a user / password combination and prevents credential brute forcing attempts using `fail2ban`. This also counts for Admin UI (**tcp/64294**) and Web UI (**tcp/64297**) access.<br>

If you do not have a SSH client at hand and still want to access the machine via command line you can do so by accessing the Admin UI from `https://<your.ip>:64294`, enter

- user: **[tsec, user you chose during post install method]**
- pass: **password you chose during the installation**

![Cockpit Terminal](doc/cockpit3.png)

<a name="kibana"></a>
## Kibana Dashboard
Just open a web browser and connect to `https://<your.ip>:64297`, enter

- user: **user you chose during the installation**
- pass: **password you chose during the installation**

and **Kibana** will automagically load. The Kibana dashboard can be customized to fit your needs. By default, we haven't added any filtering, because the filters depend on your setup. E.g. you might want to filter out your incoming administrative ssh connections and connections to update servers.

![Dashbaord](doc/kibana.png)

<a name="tools"></a>
## Tools
We included some web based management tools to improve and ease up on your daily tasks.

![Cockpit Overview](doc/cockpit1.png)
![Cockpit Containers](doc/cockpit2.png)
![Cyberchef](doc/cyberchef.png)
![ES Head Plugin](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/headplugin.png)
![Spiderfoot](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/spiderfoot.png)


<a name="maintenance"></a>
## Maintenance
As mentioned before, the system was designed to be low maintenance. Basically, there is nothing you have to do but let it run.

If you run into any problems, a reboot may fix it :bowtie:

If new versions of the components involved appear, we will test them and build new docker images. Those new docker images will be pushed to docker hub and downloaded to T-Pot and activated accordingly.  

<a name="submission"></a>
## Community Data Submission
We provide T-Pot in order to make it accessible to all parties interested in honeypot deployment. By default, the captured data is submitted to a community backend. This community backend uses the data to feed [Sicherheitstacho](https://sicherheitstacho.eu.
You may opt out of the submission by removing the `# Ewsposter service` from `/opt/tpot/etc/tpot.yml`:
1. Stop T-Pot services: `systemctl stop tpot`
2. Remove Ewsposter service: `vi /opt/tpot/etc/tpot.yml`
3. Remove the following lines, save and exit vi (`:x!`):<br>
```
# Ewsposter service
  ewsposter:
    container_name: ewsposter
    restart: always
    networks:
     - ewsposter_local
    image: "dtagdevsec/ewsposter:1810"
    volumes:
     - /data:/data
     - /data/ews/conf/ews.ip:/opt/ewsposter/ews.ip
```
4. Start T-Pot services: `systemctl start tpot`

Data is submitted in a structured ews-format, a XML stucture. Hence, you can parse out the information that is relevant to you.

We encourage you not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍

<a name="roadmap"></a>
# Roadmap
As with every development there is always room for improvements ...

Some features may be provided with updated docker images, others may require some hands on from your side.

You are always invited to participate in development on our [GitHub](https://github.com/dtag-dev-sec/tpotce) page.

<a name="disclaimer"></a>
# Disclaimer
- We don't have access to your system. So we cannot remote-assist when you break your configuration. But you can simply reinstall.
- The software was designed with best effort security, not to be in stealth mode. Because then, we probably would not be able to provide those kind of honeypot services.
- You install and you run within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- Honeypots should - by design - may not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to the community dashboard. You can disable this in the config. But hey, wouldn't it be better to contribute to the community?

<a name="faq"></a>
# FAQ
Please report any issues or questions on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues), so the community can participate.

<a name="contact"></a>
# Contact
We provide the software **as is** in a Community Edition format. T-Pot is designed to run out of the box and with zero maintenance involved. <br>
We hope you understand that we cannot provide support on an individual basis. We will try to address questions, bugs and problems on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues).

<a name="licenses"></a>
# Licenses
The software that T-Pot is built on uses the following licenses.
<br>GPLv2: [conpot)](https://github.com/mushorg/conpot/blob/master/LICENSE.txt), [dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE), [honeytrap](https://github.com/armedpot/honeytrap/blob/master/LICENSE), [suricata](http://suricata-ids.org/about/open-source/)
<br>GPLv3: [elasticpot](https://github.com/schmalle/ElasticPot), [ewsposter](https://github.com/dtag-dev-sec/ews/), [glastopf](https://github.com/glastopf/glastopf/blob/master/GPL), [rdpy](https://github.com/citronneur/rdpy/blob/master/LICENSE), [heralding](https://github.com/johnnykv/heralding/blob/master/LICENSE.txt), [snare](https://github.com/mushorg/snare/blob/master/LICENSE), [tanner](https://github.com/mushorg/snare/blob/master/LICENSE)
<br>Apache 2 License: [cyberchef](https://github.com/gchq/CyberChef/blob/master/LICENSE), [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker](https://github.com/docker/docker/blob/master/LICENSE), [elasticsearch-head](https://github.com/mobz/elasticsearch-head/blob/master/LICENCE)
<br>MIT license: [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/blob/master/LICENSE), [ctop](https://github.com/bcicen/ctop/blob/master/LICENSE), [glutton](https://github.com/mushorg/glutton/blob/master/LICENSE)
<br>zlib License: [vnclowpot](https://github.com/magisterquis/vnclowpot/blob/master/LICENSE)
<br>[cowrie](https://github.com/micheloosterhof/cowrie/blob/master/LICENSE.md)
<br>[mailoney](https://github.com/awhitehatter/mailoney)
<br>[Ubuntu licensing](http://www.ubuntu.com/about/about-ubuntu/licensing)

<a name="credits"></a>
# Credits
Without open source and the fruitful development community we are proud to be a part of, T-Pot would not have been possible! Our thanks are extended but not limited to the following people and organizations:

### The developers and development communities of

* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/graphs/contributors)
* [cockpit](https://github.com/cockpit-project/cockpit/graphs/contributors)
* [conpot](https://github.com/mushorg/conpot/graphs/contributors)
* [cowrie](https://github.com/micheloosterhof/cowrie/graphs/contributors)
* [dionaea](https://github.com/DinoTools/dionaea/graphs/contributors)
* [docker](https://github.com/docker/docker/graphs/contributors)
* [elasticpot](https://github.com/schmalle/ElasticPot/graphs/contributors)
* [elasticsearch](https://github.com/elastic/elasticsearch/graphs/contributors)
* [elasticsearch-head](https://github.com/mobz/elasticsearch-head/graphs/contributors)
* [ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors)
* [glastopf](https://github.com/mushorg/glastopf/graphs/contributors)
* [glutton](https://github.com/mushorg/glutton/graphs/contributors)
* [heralding](https://github.com/johnnykv/heralding/graphs/contributors)
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [mailoney](https://github.com/awhitehatter/mailoney)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [rdpy](https://github.com/citronneur/rdpy)
* [spiderfoot](https://github.com/smicallef/spiderfoot)
* [snare](https://github.com/mushorg/snare/graphs/contributors)
* [tanner](https://github.com/mushorg/tanner/graphs/contributors)
* [suricata](https://github.com/inliniac/suricata/graphs/contributors)
* [ubuntu](http://www.ubuntu.com/)
* [vnclowpot](https://github.com/magisterquis/vnclowpot)

### The following companies and organizations
* [canonical](http://www.canonical.com/)
* [docker](https://www.docker.com/)
* [elastic.io](https://www.elastic.co/)
* [honeynet project](https://www.honeynet.org/)
* [intel](http://www.intel.com)

### ... and of course ***you*** for joining the community!

<a name="staytuned"></a>
# Stay tuned ...
We will be releasing a new version of T-Pot about every 6-12 months.

<a name="funfact"></a>
# Fun Fact

In an effort of saving the environment we are now brewing our own Mate Ice Tea and consumed 136 liters so far for the T-Pot 18.10 development 😇
