# T-Pot - The All In One Multi Honeypot Platform

![T-Pot](doc/tpotsocial.png)

T-Pot is the all in one, optionally distributed, multiarch (amd64, arm64) honeypot plattform, supporting 20+ honeypots and countless visualization options using the Elastic Stack, animated live attack maps and lots of security tools to further improve the deception experience.
<br><br>

# TL;DR
1. Meet the [system requirements](#system-requirements). The T-Pot installation needs at least 8-16 GB RAM, 128 GB free disk space as well as a working (outgoing non-filtered) internet connection.
2. [Download](#choose-your-distro) or use a running, supported distribution.
3. Install the ISO with as minimal packages / services as possible (`ssh` required)
4. Install `curl`: `$ sudo [apt, dnf, zypper] install curl` if not installed already
5. Run installer as non-root from `$HOME`:
```
env bash -c "$(curl -sL https://github.com/telekom-security/tpotce/raw/master/install.sh)"
```
   * Follow instructions, read messages, check for possible port conflicts and reboot

<!-- TOC -->
- [T-Pot - The All In One Multi Honeypot Platform](#t-pot---the-all-in-one-multi-honeypot-platform)
- [TL;DR](#tldr)
- [Disclaimer](#disclaimer)
- [Technical Concept](#technical-concept)
  - [Honeypots and Tools](#honeypots-and-tools)
  - [Technical Architecture](#technical-architecture)
  - [Services](#services)
  - [User Types](#user-types)
- [System Requirements](#system-requirements)
  - [Running in a VM](#running-in-a-vm)
  - [Running on Hardware](#running-on-hardware)
  - [Running in a Cloud](#running-in-a-cloud)
  - [Required Ports](#required-ports)
  - [LLM-Based Honeypots](#llm-based-honeypots)
    - [Ollama](#ollama)
    - [ChatGPT](#chatgpt)
- [System Placement](#system-placement)
- [Installation](#installation)
  - [Choose your distro](#choose-your-distro)
  - [Raspberry Pi 4 (8GB) Support](#raspberry-pi-4-8gb-support)
  - [Get and install T-Pot](#get-and-install-t-pot)
  - [macOS \& Windows](#macos--windows)
  - [Installation Types](#installation-types)
    - [Standard / Hive](#standard--hive)
    - [Distributed](#distributed)
  - [Uninstall T-Pot](#uninstall-t-pot)
- [First Start](#first-start)
  - [Standalone First Start](#standalone-first-start)
  - [Distributed Deployment](#distributed-deployment)
    - [Planning and Certificates](#planning-and-certificates)
    - [Deploying Sensors](#deploying-sensors)
    - [Removing Sensors](#removing-sensors)
  - [Community Data Submission](#community-data-submission)
  - [Opt-In HPFEEDS Data Submission](#opt-in-hpfeeds-data-submission)
- [Remote Access and Tools](#remote-access-and-tools)
  - [SSH](#ssh)
  - [T-Pot Landing Page](#t-pot-landing-page)
  - [Kibana Dashboard](#kibana-dashboard)
  - [Attack Map](#attack-map)
  - [Cyberchef](#cyberchef)
  - [Elasticvue](#elasticvue)
  - [Spiderfoot](#spiderfoot)
- [Configuration](#configuration)
  - [T-Pot Config File](#t-pot-config-file)
  - [Customize T-Pot Honeypots and Services](#customize-t-pot-honeypots-and-services)
- [Maintenance](#maintenance)
  - [General Updates](#general-updates)
  - [Update Script](#update-script)
  - [Daily Reboot](#daily-reboot)
  - [Known Issues](#known-issues)
    - [Docker Images Fail to Download](#docker-images-fail-to-download)
    - [T-Pot Networking Fails](#t-pot-networking-fails)
  - [Start T-Pot](#start-t-pot)
  - [Stop T-Pot](#stop-t-pot)
  - [T-Pot Data Folder](#t-pot-data-folder)
  - [Log Persistence](#log-persistence)
  - [Factory Reset](#factory-reset)
  - [Show Containers](#show-containers)
  - [Blackhole](#blackhole)
  - [Add Users to Nginx (T-Pot WebUI)](#add-users-to-nginx-t-pot-webui)
  - [Import and Export Kibana Objects](#import-and-export-kibana-objects)
    - [Export](#export)
    - [Import](#import)
- [Troubleshooting](#troubleshooting)
  - [Logs](#logs)
  - [RAM and Storage](#ram-and-storage)
- [Contact](#contact)
  - [Issues](#issues)
  - [Discussions](#discussions)
- [Licenses](#licenses)
- [Credits](#credits)
  - [The developers and development communities of](#the-developers-and-development-communities-of)
  - [**The following companies and organizations**](#the-following-companies-and-organizations)
  - [**And of course ***YOU*** for joining the community!**](#and-of-course-you-for-joining-the-community)
- [Testimonials](#testimonials)
- [Thank you 💖](#thank-you-)
<!-- TOC -->
<br><br>

# Disclaimer
- You install and run T-Pot within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- For fast help research the [Issues](https://github.com/telekom-security/tpotce/issues) and [Discussions](https://github.com/telekom-security/tpotce/discussions).
- The software is designed and offered with best effort in mind. As a community and open source project it uses lots of other open source software and may contain bugs and issues. Report responsibly.
- Honeypots - by design - should not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to [Sicherheitstacho](https://www.sicherheitstacho.eu/start/main). You can disable this in the config (`~/tpotce/docker-compose.yml`) by [removing](#community-data-submission) the `ewsposter` section. But in this case sharing really is caring!
<br><br>

# Technical Concept
T-Pot's main components have been moved into the `tpotinit` Docker image allowing T-Pot to now support multiple Linux distributions, even macOS and Windows (although both limited to the feature set of Docker Desktop). T-Pot uses [docker](https://www.docker.com/) and [docker compose](https://docs.docker.com/compose/) to reach its goal of running as many honeypots and tools as possible simultaneously and thus utilizing the host's hardware to its maximum.
<br><br>

## Honeypots and Tools
- T-Pot offers docker images for the following honeypots:<br>
[adbhoney](https://github.com/huuck/ADBHoney),
[beelzebub](https://github.com/mariocandela/beelzebub),
[ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
[citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot),
[conpot](http://conpot.org/),
[cowrie](https://github.com/cowrie/cowrie),
[ddospot](https://github.com/aelth/ddospot),
[dicompot](https://github.com/nsmfoo/dicompot),
[dionaea](https://github.com/DinoTools/dionaea),
[elasticpot](https://gitlab.com/bontchev/elasticpot),
[endlessh](https://github.com/skeeto/endlessh),
[galah](https://github.com/0x4D31/galah),
[go-pot](https://github.com/ryanolee/go-pot),
[glutton](https://github.com/mushorg/glutton),
[h0neytr4p](https://github.com/pbssubhash/h0neytr4p),
[hellpot](https://github.com/yunginnanet/HellPot),
[heralding](https://github.com/johnnykv/heralding),
[honeyaml](https://github.com/mmta/honeyaml),
[honeypots](https://github.com/qeeqbox/honeypots),
[honeytrap](https://github.com/armedpot/honeytrap/),
[ipphoney](https://gitlab.com/bontchev/ipphoney),
[log4pot](https://github.com/thomaspatzke/Log4Pot),
[mailoney](https://github.com/awhitehatter/mailoney),
[medpot](https://github.com/schmalle/medpot),
[miniprint](https://github.com/sa7mon/miniprint),
[redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot),
[sentrypeer](https://github.com/SentryPeer/SentryPeer),
[snare](http://mushmush.org/),
[tanner](http://mushmush.org/),
[wordpot](https://github.com/gbrindisi/wordpot)

Alongside the following tools:
* [Autoheal](https://github.com/willfarrell/docker-autoheal) a tool to automatically restart containers with failed healthchecks.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [Elastic Stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticvue](https://github.com/cars10/elasticvue/) a web front end for browsing and interacting with an Elasticsearch cluster.
* [Fatt](https://github.com/0x4D31/fatt) a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
* [T-Pot-Attack-Map](https://github.com/t3chn0m4g3/t-pot-attack-map) a beautifully animated attack map for T-Pot.
* [P0f](https://lcamtuf.coredump.cx/p0f3/) is a tool for purely passive traffic fingerprinting.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) an open source intelligence automation tool.
* [Suricata](https://suricata.io/) a Network Security Monitoring engine.

... to give you the best out-of-the-box experience possible and an easy-to-use multi-honeypot system.
<br><br>


## Technical Architecture
![Architecture](doc/architecture.png)

The source code and configuration files are fully stored in the T-Pot GitHub repository. The docker images are built and preconfigured for the T-Pot environment. 

The individual Dockerfiles and configurations are located in the [docker folder](https://github.com/telekom-security/tpotce/tree/master/docker).
<br><br>

## Services
T-Pot offers a number of services which are basically divided into five groups:
1. System services provided by the OS
    * SSH for secure remote access.
2. Elastic Stack
    * Elasticsearch for storing events.
    * Logstash for ingesting, receiving and sending events to Elasticsearch.
    * Kibana for displaying events on beautifully rendered dashboards.
3. Tools
    * NGINX provides secure remote access (reverse proxy) to Kibana, CyberChef, Elasticvue, GeoIP AttackMap, Spiderfoot and allows for T-Pot sensors to securely transmit event data to the T-Pot hive.
    * CyberChef a web app for encryption, encoding, compression and data analysis.
    * Elasticvue a web front end for browsing and interacting with an Elasticsearch cluster.
    * T-Pot Attack Map a beautifully animated attack map for T-Pot.
    * Spiderfoot an open source intelligence automation tool.
4. Honeypots
    * A selection of the 23 available honeypots based on the selected `docker-compose.yml`.
5. Network Security Monitoring (NSM)
    * Fatt a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
    * P0f is a tool for purely passive traffic fingerprinting.
    * Suricata a Network Security Monitoring engine.
<br><br>

## User Types
During the installation and during the usage of T-Pot there are two different types of accounts you will be working with. Make sure you know the differences of the different account types, since it is **by far** the most common reason for authentication errors.

| Service          | Account Type | Username / Group | Description                                                        |
| :--------------- | :----------- | :--------------- | :----------------------------------------------------------------- |
| SSH              | OS           | `<OS_USERNAME>`  | The user you chose during the installation of the OS.              |
| Nginx            | BasicAuth    | `<WEB_USER>`     | `<web_user>` you chose during the installation of T-Pot.           |
| CyberChef        | BasicAuth    | `<WEB_USER>`     | `<web_user>` you chose during the installation of T-Pot.           |
| Elasticvue       | BasicAuth    | `<WEB_USER>`     | `<web_user>` you chose during the installation of T-Pot.           |
| Geoip Attack Map | BasicAuth    | `<WEB_USER>`     | `<web_user>` you chose during the installation of T-Pot.           |
| Spiderfoot       | BasicAuth    | `<WEB_USER>`     | `<web_user>` you chose during the installation of T-Pot.           |
| T-Pot            | OS           | `tpot`           | `tpot` this user / group is always reserved by the T-Pot services. |
| T-Pot Logs       | BasicAuth    | `<LS_WEB_USER>`  | `LS_WEB_USER` are automatically managed.                           |


<br><br>

# System Requirements

Depending on the [supported Linux distro images](#choose-your-distro), hive / sensor, installing on [real hardware](#running-on-hardware), in a [virtual machine](#running-in-a-vm) or other environments there are different kind of requirements to be met regarding OS, RAM, storage and network for a successful installation of T-Pot (you can always adjust `~/tpotce/docker-compose.yml` and `~/tpotce/.env`to your needs to overcome these requirements).
<br><br>

| T-Pot Type | RAM  | Storage   | Description                                                                                      |
| :--------- | :--- | :-------- | :----------------------------------------------------------------------------------------------- |
| Hive       | 16GB | 256GB SSD | As a rule of thumb, the more honeypots, sensors & data, the more RAM and storage is needed.      |
| Sensor     | 8GB  | 128GB SSD | Since honeypot logs are persisted (~/tpotce/data) for 30 days, storage depends on attack volume. |

T-Pot does require ...
- an IPv4 address via DHCP or statically assigned
- a working, non-proxied, internet connection
... for a successful installation and operation.
<br><br>
*If you need proxy support or otherwise non-standard features, you should check the docs of the [supported Linux distro images](#choose-your-distro) and / or the [Docker documentation](https://docs.docker.com/).*
<br><br>

## Running in a VM
All of the [supported Linux distro images](#choose-your-distro) will run in a VM which means T-Pot will just run fine. The following were tested / reported to work:
* [UTM (Intel & Apple Silicon)](https://mac.getutm.app/)
* [VirtualBox](https://www.virtualbox.org/)
* [VMWare Fusion](https://www.vmware.com/products/fusion/fusion-evaluation.html) and [VMWare Workstation](https://www.vmware.com/products/workstation-pro.html)
* KVM is reported to work as well.

***Some configuration / setup hints:***
- While Intel versions run stable, Apple Silicon (arm64) support has known issues which in UTM may require switching `Display` to `Console Only` during initial installation of the OS and afterwards back to `Full Graphics`.
- During configuration you may need to enable promiscuous mode for the network interface in order for fatt, suricata and p0f to work properly.
- If you want to use a wifi card as a primary NIC for T-Pot, please be aware that not all network interface drivers support all wireless cards. In VirtualBox e.g. you have to choose the *"MT SERVER"* model of the NIC.
<br><br>

## Running on Hardware
T-Pot is only limited by the hardware support of the [supported Linux distro images](#choose-your-distro). It is recommended to check the HCL (hardware compatibility list) and test the supported distros with T-Pot before investing in dedicated hardware.
<br><br>

## Running in a Cloud
T-Pot is tested on and known to run on ...
* Telekom OTC using the post install method
... others may work, but remain untested. 

Some users report working installations on other clouds and hosters, i.e. Azure and GCP. Hardware requirements may be different. If you are unsure you should research [issues](https://github.com/telekom-security/tpotce/issues) and [discussions](https://github.com/telekom-security/tpotce/discussions) and run some functional tests. With T-Pot 24.04.0 and forward we made sure to remove settings that were known to interfere with cloud based installations. 
<br><br>

## Required Ports
Besides the ports generally needed by the OS, i.e. obtaining a DHCP lease, DNS, etc. T-Pot will require the following ports for incoming / outgoing connections. Review the [T-Pot Architecture](#technical-architecture) for a visual representation. Also some ports will show up as duplicates, which is fine since used in different editions.

| Port                                                                                                                                  | Protocol | Direction | Description                                                                                         |
| :------------------------------------------------------------------------------------------------------------------------------------ | :------- | :-------- | :-------------------------------------------------------------------------------------------------- |
| 80, 443                                                                                                                               | tcp      | outgoing  | T-Pot Management: Install, Updates, Logs (i.e. OS, GitHub, DockerHub, Sicherheitstacho, etc.        |
| 11434                                                                                                                                 | tcp      | outgoing  | LLM based honeypots: Access your Ollama installation                                                |
| 64294                                                                                                                                 | tcp      | incoming  | T-Pot Management: Sensor data transmission to hive (through NGINX reverse proxy) to 127.0.0.1:64305 |
| 64295                                                                                                                                 | tcp      | incoming  | T-Pot Management: Access to SSH                                                                     |
| 64297                                                                                                                                 | tcp      | incoming  | T-Pot Management Access to NGINX reverse proxy                                                      |
| 5555                                                                                                                                  | tcp      | incoming  | Honeypot: ADBHoney                                                                                  |
| 22                                                                                                                                    | tcp      | incoming  | Honeypot: Beelzebub  (LLM required)                                                                 |
| 5000                                                                                                                                  | udp      | incoming  | Honeypot: CiscoASA                                                                                  |
| 8443                                                                                                                                  | tcp      | incoming  | Honeypot: CiscoASA                                                                                  |
| 443                                                                                                                                   | tcp      | incoming  | Honeypot: CitrixHoneypot                                                                            |
| 80, 102, 502, 1025, 2404, 10001, 44818, 47808, 50100                                                                                  | tcp      | incoming  | Honeypot: Conpot                                                                                    |
| 161, 623                                                                                                                              | udp      | incoming  | Honeypot: Conpot                                                                                    |
| 22, 23                                                                                                                                | tcp      | incoming  | Honeypot: Cowrie                                                                                    |
| 19, 53, 123, 1900                                                                                                                     | udp      | incoming  | Honeypot: Ddospot                                                                                   |
| 11112                                                                                                                                 | tcp      | incoming  | Honeypot: Dicompot                                                                                  |
| 21, 42, 135, 443, 445, 1433, 1723, 1883, 3306, 8081                                                                                   | tcp      | incoming  | Honeypot: Dionaea                                                                                   |
| 69                                                                                                                                    | udp      | incoming  | Honeypot: Dionaea                                                                                   |
| 9200                                                                                                                                  | tcp      | incoming  | Honeypot: Elasticpot                                                                                |
| 22                                                                                                                                    | tcp      | incoming  | Honeypot: Endlessh                                                                                  |
| 80, 443, 8080, 8443                                                                                                                   | tcp      | incoming  | Honeypot: Galah  (LLM required)                                                                     |
| 8080                                                                                                                                  | tcp      | incoming  | Honeypot: Go-pot                                                                                    |
| 80, 443                                                                                                                               | tcp      | incoming  | Honeypot: H0neytr4p                                                                                 |
| 21, 22, 23, 25, 80, 110, 143, 443, 993, 995, 1080, 5432, 5900                                                                         | tcp      | incoming  | Honeypot: Heralding                                                                                 |
| 3000                                                                                                                                  | tcp      | incoming  | Honeypot: Honeyaml                                                                                  |
| 21, 22, 23, 25, 80, 110, 143, 389, 443, 445, 631, 1080, 1433, 1521, 3306, 3389, 5060, 5432, 5900, 6379, 6667, 8080, 9100, 9200, 11211 | tcp      | incoming  | Honeypot: qHoneypots                                                                                |
| 53, 123, 161, 5060                                                                                                                    | udp      | incoming  | Honeypot: qHoneypots                                                                                |
| 631                                                                                                                                   | tcp      | incoming  | Honeypot: IPPHoney                                                                                  |
| 80, 443, 8080, 9200, 25565                                                                                                            | tcp      | incoming  | Honeypot: Log4Pot                                                                                   |
| 25                                                                                                                                    | tcp      | incoming  | Honeypot: Mailoney                                                                                  |
| 2575                                                                                                                                  | tcp      | incoming  | Honeypot: Medpot                                                                                    |
| 9100                                                                                                                                  | tcp      | incoming  | Honeypot: Miniprint                                                                                 |
| 6379                                                                                                                                  | tcp      | incoming  | Honeypot: Redishoneypot                                                                             |
| 5060                                                                                                                                  | tcp/udp  | incoming  | Honeypot: SentryPeer                                                                                |
| 80                                                                                                                                    | tcp      | incoming  | Honeypot: Snare (Tanner)                                                                            |
| 8090                                                                                                                                  | tcp      | incoming  | Honeypot: Wordpot                                                                                   |


Ports and availability of SaaS services may vary based on your geographical location.

For some honeypots to reach full functionality (i.e. Cowrie or Log4Pot) outgoing connections are necessary as well, in order for them to download the attacker's malware. Please see the individual honeypot's documentation to learn more by following the [links](#technical-concept) to their repositories.

## LLM-Based Honeypots
We think LLM-Based Honeypots mark the **beginning** of a game change for the deception / honeypot field. Consequently, starting with the release of **T-Pot 24.04.1**, two LLM-based honeypots, **Beelzebub** and **Galah**, have been introduced. These honeypots require an installation of **Ollama**, which needs to be configured in the [T-Pot configuration file](#t-pot-config-file). You can also adjust the settings in this file for **ChatGPT** support, but note that changes will also be required in the docker compose file (`~/tpotce/compose/llm.yml`) to accommodate these adjustments.<br><br>
Follow the links in the [Honeypots and Tools](#honeypots-and-tools) section to find out more about **Beelzebub** and **Galah**.

### Ollama
🚨 **CPU-based usage is not recommended**, not even for testing.<br><br>
To set up and run **Ollama**, refer to the [Ollama GitHub repository](https://github.com/ollama/ollama) for instructions. For entry-level or testing purposes, results can be achieved using a **Nvidia RTX 4060 Ti 16GB** or equivalent (AMD's ROCm is also supported by Ollama), with models like **openchat** and **Llama3**. As a general rule with LLM-based systems, the better and more hardware you use, the faster and more accurate the results will be, especially when tasks are offloaded to multiple GPUs and larger models.

### ChatGPT
ChatGPT support for these honeypots will remain untested in relation to T-Pot.
<br><br>

# System Placement
It is recommended to get yourself familiar with how T-Pot and the honeypots work before you start exposing towards the internet. For a quickstart run a T-Pot installation in a virtual machine.
<br><br>
Once you are familiar with how things work you should choose a network you suspect intruders in or from (i.e. the internet). Otherwise T-Pot will most likely not capture any attacks (unless you want to prove a point)! For starters it is recommended to put T-Pot in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface. To avoid probing for T-Pot's management ports you should put T-Pot behind a firewall and forward all TCP / UDP traffic in the port range of 1-64000 to T-Pot while allowing access to ports > 64000 only from trusted IPs and / or only expose the [ports](#required-ports) relevant to your use-case. If you wish to catch malware traffic on unknown ports you should not limit the ports you forward since glutton and honeytrap dynamically bind any TCP port that is not occupied by other honeypot daemons and thus give you a better representation of the risks your setup is exposed to.
<br><br>

# Installation
[Download](#choose-your-distro) one of the [supported Linux distro images](#choose-your-distro), follow the [TL;DR](#tldr) instructions or `git clone` the T-Pot repository and run the installer `~/tpotce/install.sh`. Running T-Pot on top of a running and supported Linux system is possible, but a clean installation is recommended to avoid port conflicts with running services. The T-Pot installer will require direct access to the internet as described [here](#required-ports).
<br><br>

## Choose your distro
**Steps to Follow:**

1. Download a supported Linux distribution from the list below.
2. During installation choose a **minimum**, **netinstall** or **server** version that will only install essential packages.
3. **Never** install a graphical desktop environment such as Gnome or KDE. T-Pot will fail to work with it due to port conflicts. 
4. Make sure to install SSH, so you can connect to the machine remotely.


| Distribution Name                                                                  | x64                                                                                                                                   | arm64                                                                                                                                   |
| :--------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------- |
| [Alma Linux OS 9.5 Boot ISO](https://almalinux.org)                                | [download](https://repo.almalinux.org/almalinux/9.5/isos/x86_64/AlmaLinux-9.5-x86_64-boot.iso)                                        | [download](https://repo.almalinux.org/almalinux/9.5/isos/aarch64/AlmaLinux-9.5-aarch64-boot.iso)                                        |
| [Debian 12 Network Install](https://www.debian.org/CD/netinst/index.en.html)       | [download](https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-12.9.0-amd64-netinst.iso)                                 | [download](https://cdimage.debian.org/debian-cd/current/arm64/iso-cd/debian-12.9.0-arm64-netinst.iso)                                   |
| [Fedora Server 41 Network Install](https://fedoraproject.org/server/download)      | [download](https://download.fedoraproject.org/pub/fedora/linux/releases/41/Server/x86_64/iso/Fedora-Server-netinst-x86_64-41-1.4.iso) | [download](https://download.fedoraproject.org/pub/fedora/linux/releases/41/Server/aarch64/iso/Fedora-Server-netinst-aarch64-41-1.4.iso) |
| [OpenSuse Tumbleweed Network Image](https://get.opensuse.org/tumbleweed/#download) | [download](https://download.opensuse.org/tumbleweed/iso/openSUSE-Tumbleweed-NET-x86_64-Current.iso)                                   | [download](https://download.opensuse.org/ports/aarch64/tumbleweed/iso/openSUSE-Tumbleweed-NET-aarch64-Current.iso)                      |
| [Rocky Linux OS 9.5 Boot ISO](https://rockylinux.org/download)                     | [download](https://download.rockylinux.org/pub/rocky/9/isos/x86_64/Rocky-9.5-x86_64-minimal.iso)                                      | [download](https://download.rockylinux.org/pub/rocky/9/isos/aarch64/Rocky-9.5-aarch64-minimal.iso)                                      |
| [Ubuntu 24.04.1 Live Server](https://ubuntu.com/download/server)                   | [download](https://releases.ubuntu.com/24.04/ubuntu-24.04.1-live-server-amd64.iso)                                                    | [download](https://cdimage.ubuntu.com/releases/24.04/release/ubuntu-24.04.1-live-server-arm64.iso)                                      |

<br>

## Raspberry Pi 4 (8GB) Support
| Distribution Name                                                | arm64                                                                                                                                               |
| :--------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Raspberry Pi OS (**64Bit, Lite**)](https://www.raspberrypi.com) | [download](https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2024-03-15/2024-03-15-raspios-bookworm-arm64-lite.img.xz) |

<br><br> 
## Get and install T-Pot
1. Clone the GitHub repository: `$ git clone https://github.com/telekom-security/tpotce` or follow the [TL;DR](#tldr) and skip this section.
2. Change into the **tpotce/** folder: `$ cd tpotce`
3. Run the installer as non-root: `$ ./install.sh`:
   * ⚠️ ***Depending on your Linux distribution of choice the installer will:***
     * Change the SSH port to `tcp/64295`
     * Disable the DNS Stub Listener to avoid port conflicts with honeypots
     * Set SELinux to Monitor Mode
     * Set the firewall target for the public zone to ACCEPT
     * Add Docker's repository and install Docker
     * Install recommended packages
     * Remove packages known to cause issues
     * Add the current user to the docker group (allow docker interaction without `sudo`)
     * Add `dps` and `dpsw` aliases (`grc docker ps -a`, `watch -c "grc --colour=on docker ps -a`)
     * Add `la`, `ll` and `ls` aliases (for `exa`, a improved `ls` command)
     * Add `mi` (for `micro`, a great alternative to `vi` and / or `nano`)
     * Display open ports on the host (compare with T-Pot [required](https://github.com/telekom-security/tpotce#required-ports) ports)
     * Add and enable `tpot.service` to `/etc/systemd/system` so T-Pot can automatically start and stop
4. Follow the installer instructions, you will have to enter your user (`sudo` or `root`) password at least once
5. Check the installer messages for errors and open ports that might cause port conflicts
6. Reboot: `$ sudo reboot`
<br><br>

## macOS & Windows
Sometimes it is just nice if you can spin up a T-Pot instance on macOS or Windows, i.e. for development, testing or just the fun of it. As Docker Desktop is rather limited not all honeypot types or T-Pot features are supported. Also remember, by default the macOS and Windows firewall are blocking access from remote, so testing is limited to the host. For production it is recommended to run T-Pot on [Linux](#choose-your-distro).<br>
To get things up and running just follow these steps:
1. Install Docker Desktop for [macOS](https://docs.docker.com/desktop/install/mac-install/) or [Windows](https://docs.docker.com/desktop/install/windows-install/).
2. Clone the GitHub repository: `git clone https://github.com/telekom-security/tpotce` (in Windows make sure the code is checked out with `LF` instead of `CRLF`!)
3. Go to: `cd ~/tpotce`
4. Copy `cp compose/mac_win.yml ./docker-compose.yml`
5. Create a `WEB_USER` by running `~/tpotce/genuser.sh` (macOS) or `~/tpotce/genuserwin.ps1` (Windows)
6. Adjust the `.env` file by changing `TPOT_OSTYPE=linux` to either `mac` or `win`:
   ```
   # OSType (linux, mac, win)
   #  Most docker features are available on linux
   TPOT_OSTYPE=mac
   ```
7. You have to ensure on your own there are no port conflicts keeping T-Pot from starting up.
8. Start T-Pot: `docker compose up` or `docker compose up -d` if you want T-Pot to run in the background.
9. Stop T-Pot: `CTRL-C` (it if was running in the foreground) and / or `docker compose down -v` to stop T-Pot entirely.

## Installation Types

### Standard / Hive
With T-Pot Standard / Hive all services, tools, honeypots, etc. will be installed on to a single host which also serves as a Hive endpoint. Make sure to meet the [system requirements](#system-requirements). You can adjust `~/tpotce/docker-compose.yml` to your personal use-case or create your very own configuration using `~/tpotce/compose/customizer.py` for a tailored T-Pot experience to your needs.
Once the installation is finished you can proceed to [First Start](#first-start).
<br><br>

### Distributed
The distributed version of T-Pot requires at least two hosts
- the T-Pot **Hive**, the standard installation of T-Pot (install this first!),
- and a T-Pot **Sensor**, which will host only the honeypots, some tools and transmit log data to the **Hive**.
- The **Sensor** will not start before finalizing the **Sensor** installation as described in [Distributed Deployment](#distributed-deployment).
<br><br>

## Uninstall T-Pot
Uninstallation of T-Pot is only available on the [supported Linux distros](#choose-your-distro).<br>
To uninstall T-Pot run `~/tpotce/uninstall.sh` and follow the uninstaller instructions, you will have to enter your password at least once.<br>
Once the uninstall is finished reboot the machine `sudo reboot`
<br><br>

# First Start
Once the T-Pot Installer successfully finishes, the system needs to be rebooted (`sudo reboot`). Once rebooted you can log into the system using the user you setup during the installation of the system. Logins are according to the [User Types](#user-types):

- user: **[`<OS_USERNAME>`]**
- pass: **[password]**

You can login via SSH to access the command line: `ssh -l <OS_USERNAME> -p 64295 <your.ip>`:

- user: **[`<OS_USERNAME>`]**
- pass: **[password, ssh key recommended]**

You can also login from your browser and access the T-Pot WebUI and tools: `https://<your.ip>:64297`
- user: **[`<WEB_USER>`]**
- pass: **[password]**
<br><br>

## Standalone First Start
There is not much to do except to login and check via `dps` if all services and honeypots are starting up correctly and login to Kibana and / or Geoip Attack Map to monitor the attacks.
<br><br>

## Distributed Deployment
### Planning and Certificates
The distributed deployment involves planning as **T-Pot Init** will only create a self-signed certificate for the IP of the **Hive** host which usually is suitable for simple setups. Since **logstash** will check for a valid certificate upon connection, a distributed setup involving **Hive** to be reachable on multiple IPs (i.e. RFC 1918 and public NAT IP) and maybe even a domain name will result in a connection error where the certificate cannot be validated as such a setup needs a certificate with a common name and SANs (Subject Alternative Name).<br>
Before deploying any sensors make sure you have planned out domain names and IPs properly to avoid issues with the certificate. For more details see [issue #1543](https://github.com/telekom-security/tpotce/issues/1543).<br>
Adjust the example to your IP / domain setup and follow the commands to change the certificate of **Hive**:

```
sudo systemctl stop tpot

sudo openssl req \
    -nodes \
    -x509 \
    -sha512 \
    -newkey rsa:8192 \
    -keyout "$HOME/tpotce/data/nginx/cert/nginx.key" \
    -out "$HOME/tpotce/data/nginx/cert/nginx.crt" \
    -days 3650 \
    -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' \
    -addext "subjectAltName = IP:192.168.1.200, IP:1.2.3.4, DNS:my.primary.domain, DNS:my.secondary.domain"
    
sudo chmod 774 $HOME/tpotce/data/nginx/cert/*
sudo chown tpot:tpot $HOME/tpotce/data/nginx/cert/*

sudo systemctl start tpot
```

The T-Pot configuration file (`.env`) does allow to disable the SSL verification for logstash connections from **Sensor** to the **Hive** by setting `LS_SSL_VERIFICATION=none`. For security reasons this is only recommended for lab or test environments.<br><br>
If you choose to use a valid certificate for the **Hive** signed by a CA (i.e. Let's Encrypt), logstash, and therefore the **Sensor**, should have no problems to connect and transmit its logs to the **Hive**.

### Deploying Sensors
Once you have rebooted the **Sensor** as instructed by the installer you can continue with the distributed deployment by logging into **Hive** and go to `cd ~/tpotce` folder. Make sure you understood the [Planning and Certificates](#planning-and-certificates) before continuing with the actual deployment.

If you have not done already generate a SSH key to securely login to the **Sensor** and to allow `Ansible` to run a playbook on the sensor:
1. Run `ssh-keygen`, follow the instructions and leave the passphrase empty:
   ```
   Generating public/private rsa key pair.
   Enter file in which to save the key (/home/<your_user>/.ssh/id_rsa):
   Enter passphrase (empty for no passphrase):
   Enter same passphrase again:
   Your identification has been saved in /home/<your_user>/.ssh/id_rsa
   Your public key has been saved in /home/<your_user>/.ssh/id_rsa.pub
   ```
2. Deploy the key to the Sensor by running `ssh-copy-id -p 64295 <Sensor_SSH_USER>@<Sensor_IP>)`:
   ```
   /usr/bin/ssh-copy-id: INFO: Source of key(s) to be installed: "/home/<your_user>/.ssh/id_rsa.pub"
   The authenticity of host '[<Sensor_IP>]:64295 ([<Sensor_IP>]:64295)' can't be stablished.
   ED25519 key fingerprint is SHA256:naIDxFiw/skPJadTcgmWZQtgt+CdfRbUCoZn5RmkOnQ.
   This key is not known by any other names.
   Are you sure you want to continue connecting (yes/no/[fingerprint])? yes
   /usr/bin/ssh-copy-id: INFO: attempting to log in with the new key(s), to filter out any that are already installed
   /usr/bin/ssh-copy-id: INFO: 1 key(s) remain to be installed -- if you are prompted now it is to install the new keys
   <your_user>@172.20.254.124's password:
  
   Number of key(s) added: 1
  
   Now try logging into the machine, with:   "ssh -p '64295' '<your_user>@<Sensor_IP>'"
   and check to make sure that only the key(s) you wanted were added.
   ```
3. As suggested follow the instructions to test the connection `ssh -p '64295' '<your_user>@<Sensor_IP>'`.
4. Once the key is successfully deployed run `./deploy.sh` and follow the instructions.
<br><br>

### Removing Sensors
Identify the `TPOT_HIVE_USER` ENV on the Sensor in the `$HOME/tpotce/.env` config (it is a base64 encoded string). Now identify the same string in the `LS_WEB_USER` ENV on the Hive in the `$HOME/tpotce/.env` config. Remove the string and restart T-Pot.<br>
Now you can safely delete the Sensor machine.

## Community Data Submission
T-Pot is provided in order to make it accessible to everyone interested in honeypots. By default, the captured data is submitted to a community backend. This community backend uses the data to feed [Sicherheitstacho](https://sicherheitstacho.eu).
You may opt out of the submission by removing the `# Ewsposter service` from `~/tpotce/docker-compose.yml` by following these steps:
1. Stop T-Pot services: `systemctl stop tpot`
2. Open `~/tpotce/docker-compose.yml`: `micro ~/tpotce/docker-compose.yml`
3. Remove the following lines, save and exit micro (`CTRL+Q`):
```
# Ewsposter service
  ewsposter:
    container_name: ewsposter
    restart: always
    depends_on:
      tpotinit:
        condition: service_healthy
    networks:
     - ewsposter_local
    environment:
     - EWS_HPFEEDS_ENABLE=false
     - EWS_HPFEEDS_HOST=host
     - EWS_HPFEEDS_PORT=port
     - EWS_HPFEEDS_CHANNELS=channels
     - EWS_HPFEEDS_IDENT=user
     - EWS_HPFEEDS_SECRET=secret
     - EWS_HPFEEDS_TLSCERT=false
     - EWS_HPFEEDS_FORMAT=json
    image: ${TPOT_REPO}/ewsposter:${TPOT_VERSION}
    pull_policy: ${TPOT_PULL_POLICY}
    volumes:
     - ${TPOT_DATA_PATH}:/data
     - ${TPOT_DATA_PATH}/ews/conf/ews.ip:/opt/ewsposter/ews.ip
```
4. Start T-Pot services: `systemctl start tpot`

It is encouraged not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍
<br><br>

## Opt-In HPFEEDS Data Submission
As an Opt-In it is possible to share T-Pot data with 3rd party HPFEEDS brokers.
1. Follow the instructions [here](#community-data-submission) to stop the T-Pot services and open `~/tpotce/docker-compose.yml`.
2. Scroll down to the `ewsposter` section and adjust the HPFEEDS settings to your needs.
3. If you need to add a CA certificate add it to `~/tpotce/data/ews/conf` and set  `EWS_HPFEEDS_TLSCERT=/data/ews/conf/<your_ca.crt>`.
4. Start T-Pot services: `systemctl start tpot`.
<br><br>

# Remote Access and Tools
Remote access to your host / T-Pot is possible with SSH (on **`tcp/64295`**) and some services and tools come with T-Pot to make some of your research tasks a lot easier.
<br><br>

## SSH
According to the [User Types](#user-types) you can login via SSH to access the command line: `ssh -l <OS_USERNAME> -p 64295 <your.ip>`:

- user: **[`<OS_USERNAME>`]**
- pass: **[password]**
<br><br>

## T-Pot Landing Page 
According to the [User Types](#user-types) you can open the T-Pot Landing Page from your browser via `https://<your.ip>:64297`:

- user: **[`<WEB_USER>`]**
- pass: **[password]**

![T-Pot-WebUI](doc/tpotwebui.png)
<br><br>

## Kibana Dashboard
On the T-Pot Landing Page just click on `Kibana` and you will be forwarded to Kibana. You can select from a large variety of dashboards and visualizations all tailored to the T-Pot supported honeypots.

![Dashbaord](doc/kibana_a.png)
<br><br>

## Attack Map
On the T-Pot Landing Page just click on `Attack Map` and you will be forwarded to the Attack Map. Since the Attack Map utilizes web sockets you may need to re-enter the `<WEB_USER>` credentials.

![AttackMap](doc/attackmap.png)
<br><br>

## Cyberchef
On the T-Pot Landing Page just click on `Cyberchef` and you will be forwarded to Cyberchef.

![Cyberchef](doc/cyberchef.png)
<br><br>

## Elasticvue
On the T-Pot Landing Page just click on `Elasticvue` and you will be forwarded to Elasticvue.

![Elasticvue](doc/elasticvue.png)
<br><br>

## Spiderfoot
On the T-Pot Landing Page just click on `Spiderfoot` and you will be forwarded to Spiderfoot.

![Spiderfoot](doc/spiderfoot.png)
<br><br>

# Configuration

## T-Pot Config File
T-Pot offers a configuration file providing variables not only for the docker services (i.e. honeypots and tools) but also for the docker compose environment. The configuration file is hidden in `~/tpoce/.env`. There is also an example file (`env.example`) which holds the default configuration.<br>
Before the first start run `~/tpotce/genuser.sh` or setup the `WEB_USER` manually as described [here](#add-users-to-nginx-t-pot-webui). 

## Customize T-Pot Honeypots and Services

In `~/tpotce/compose` you will find everything you need to adjust the T-Pot Standard / Hive installation:
```
customizer.py
llm.yml
mac_win.yml
mini.yml
mobile.yml
sensor.yml
standard.yml
tarpit.yml
tpot_services.yml
```
The `.yml` files are docker compose files, each representing a different set of honeypots and tools with `tpot_services.yml` being a template for `customizer.py` to create a customized docker compose file.<br><br>
To activate a compose file follow these steps:
1. Stop T-Pot with `systemctl stop tpot`.
2. Copy the docker compose file `cp ~/tpotce/compose/<dockercompose.yml> ~/tpotce/docker-compose.yml`.
3. Start T-Pot with `systemctl start tpot`.

To create your customized docker compose file:
1. Go to `cd ~/tpotce/compose`.
2. Run `python3 customizer.py`.
3. The script will guide you through the process of creating your own `docker-compose.yml`. As some honeypots and services occupy the same ports it will check if any port conflicts are present and notify regarding the conflicting services. You then can resolve them manually by adjusting `docker-compose-custom.yml` or re-run the script.
4. Stop T-Pot with `systemctl stop tpot`.
5. Copy the custom docker compose file: `cp docker-compose-custom.yml ~/tpotce` and `cd ~/tpotce`.
6. Check if everything works by running `docker-compose -f docker-compose-custom.yml up`. In case of errors follow the [Docker Compose Specification](https://docs.docker.com/compose/compose-file/) for mitigation. Most likely it is just a port conflict you can adjust by editing the docker compose file. 
6. If everything works just fine press `CTRL-C` to stop the containers and run `docker-compose -f docker-compose-custom.yml down -v`.
7. Replace docker compose file with the new and successfully tested customized docker compose file `mv ~/tpotce/docker-compose-custom.yml ~/tpotce/docker-compose.yml`.
8. Start T-Pot with `systemctl start tpot`.
<br><br>

# Maintenance
T-Pot is designed to be low maintenance. Since almost everything is provided through docker images there is basically nothing you have to do but let it run. We will upgrade the docker images regularly to reduce the risks of compromise; however you should read this section closely.<br><br>
Should an update fail, opening an issue or a discussion will help to improve things in the future, but the offered solution will ***always*** be to perform a ***fresh install*** as we simply ***cannot*** provide any support for lost data!
<br><br>

## General Updates
T-Pot security depends on the updates provided for the [supported Linux distro images](#choose-your-distro). Make sure to review the OS documentation and ensure updates are installed regularly by the OS. By default (`~/tpotce/.env`) `TPOT_PULL_POLICY=always` will ensure that at every T-Pot start docker will check for new docker images and download them before creating the containers.
<br><br>

## Update Script
T-Pot releases are offered through GitHub and can be pulled using `~/tpotce/update.sh`.<br> 
***If you made any relevant changes to the T-Pot config files make sure to create a backup first!***<br>
***Updates may have unforeseen consequences. Create a backup of the machine or the files most valuable to your work!***<br>

The update script will ...
 - ***mercilessly*** overwrite local changes to be in sync with the T-Pot master branch
 - create a full backup of the `~/tpotce` folder
 - update all files in `~/tpotce` to be in sync with the T-Pot master branch
 - restore your custom `ews.cfg` from `~/tpotce/data/ews/conf` and the T-Pot configuration (`~/tpotce/.env`).

## Daily Reboot
By default T-Pot will add a daily reboot including some cleaning up. You can adjust this line with `sudo crontab -e` 
```
#Ansible: T-Pot Daily Reboot
42 2 * * * bash -c 'systemctl stop tpot.service && docker container prune -f; docker image prune -f; docker volume prune -f; /usr/sbin/shutdown -r +1 "T-Pot Daily Reboot"'
```

## Known Issues
The following issues are known, simply follow the described steps to solve them.
<br><br>

### Docker Images Fail to Download
Some time ago Docker introduced download [rate limits](https://docs.docker.com/docker-hub/download-rate-limit/#:~:text=Docker%20Hub%20limits%20the%20number,pulls%20per%206%20hour%20period.). If you are frequently downloading Docker images via a single or shared IP, the IP address might have exhausted the Docker download rate limit. Login to your Docker account to extend the rate limit.
```
sudo su -
docker login
```

### T-Pot Networking Fails
T-Pot is designed to only run on machines with a single NIC. T-Pot will try to grab the interface with the default route, however it is not guaranteed that this will always succeed. At best use T-Pot on machines with only a single NIC.

## Start T-Pot
The T-Pot service automatically starts and stops on each reboot (which occurs once on a daily basis as setup in `sudo crontab -l` during installation).
<br>
If you want to manually start the T-Pot service you can do so via `systemctl start tpot` and observe via `dpsw` the startup of the containers.
<br><br>

## Stop T-Pot
The T-Pot service automatically starts and stops on each reboot (which occurs once on a daily basis as setup in `sudo crontab -l` during installation).
<br>
If you want to manually stop the T-Pot service you can do so via `systemctl stop tpot` and observe via `dpsw` the shutdown of the containers.
<br><br>

## T-Pot Data Folder
All persistent log files from the honeypots, tools and T-Pot related services are stored in `~/tpotce/data`. This includes collected artifacts which are not transmitted to the Elastic Stack.
<br><br>

## Log Persistence
All log data stored in the [T-Pot Data Folder](#t-pot-data-folder) will be persisted for 30 days by default.
<br>
Elasticsearch indices are handled by the `tpot` Index Lifecycle Policy which can be adjusted directly in Kibana (make sure to "Include managed system policies").
![IndexManagement1](doc/kibana_b.png)
<br><br>

By default the `tpot` Index Lifecycle Policy keeps the indices for 30 days. This offers a good balance between storage and speed. However you may adjust the policy to your needs.
![IndexManagement2](doc/kibana_c.png)
<br><br>

## Factory Reset
All log data stored in the [T-Pot Data Folder](#t-pot-data-folder) (except for Elasticsearch indices, of course) can be erased by running `clean.sh`.
Sometimes things might break beyond repair and it has never been easier to reset a T-Pot to factory defaults (make sure to enter `cd ~/tpotce`).
1. Stop T-Pot using `systemctl stop tpot`.
2. Move / Backup the `~/tpotce/data` folder to a safe place (this is optional, just in case).
3. Delete the `~/tpotce/data` folder using `sudo rm -rf ~/tpotce/data`.
4. Reset T-Pot to the last fetched commit:
```
cd ~/tpotce/
git reset --hard
```
5. Now you can run `~/tpotce/install.sh`.
<br><br>

## Show Containers
You can show all T-Pot relevant containers by running `dps` or `dpsw [interval]`. The `interval (s)` will re-run `dps` periodically.
<br><br>

## Blackhole
Blackhole will run T-Pot in kind of a stealth mode manner without permanent visits of publicly known scanners and thus reducing the possibility of being exposed. While this is of course always a cat and mouse game the blackhole feature is null routing all requests from [known mass scanners](https://raw.githubusercontent.com/stamparm/maltrail/master/trails/static/mass_scanner.txt) while still catching the events through Suricata.
<br>
The feature is activated by setting `TPOT_BLACKHOLE=DISABLED` in `~/tpotce/.env`, then run `systemctl stop tpot` and `systemctl start tpot` or `sudo reboot`.
<br>
Enabling this feature will drastically reduce attackers visibility and consequently result in less activity. However as already mentioned it is neither a guarantee for being completely stealth nor will it prevent fingerprinting of some honeypot services.
<br><br>

## Add Users to Nginx (T-Pot WebUI)
Nginx (T-Pot WebUI) allows you to add as many `<WEB_USER>` accounts as you want (according to the [User Types](#user-types)).<br>
To **add** a new user run `~/tpotce/genuser.sh`.<br>
To **remove** users open `~/tpotce/.env`, locate `WEB_USER` and remove the corresponding base64 string (to decode: `echo <base64_string> | base64 -d`, or open CyberChef and load "From Base64" recipe).<br>
For the changes to take effect you need to restart T-Pot using `systemctl stop tpot` and `systemctl start tpot` or `sudo reboot`.
<br><br>

## Import and Export Kibana Objects
Some T-Pot updates will require you to update the Kibana objects. Either to support new honeypots or to improve existing dashboards or visualizations. Make sure to ***export*** first so you do not loose any of your adjustments.

### Export
1. Go to Kibana
2. Click on "Stack Management"
3. Click on "Saved Objects"
4. Click on "Export <no.> objects"
5. Click on "Export all"
This will export a NDJSON file with all your objects. Always run a full export to make sure all references are included.

### Import
1. [Download the NDJSON file](https://github.com/dtag-dev-sec/tpotce/blob/master/docker/tpotinit/dist/etc/objects/kibana_export.ndjson.zip) and unzip it.
2. Go to Kibana
3. Click on "Stack Management"
4. Click on "Saved Objects"
5. Click on "Import" and leave the defaults (check for existing objects and automatically overwrite conflicts) if you did not make personal changes to the Kibana objects.
6. Browse for NDJSON file
When asked: "If any of the objects already exist, do you want to automatically overwrite them?" you answer with "Yes, overwrite all".
<br><br>

# Troubleshooting
Generally T-Pot is offered ***as is*** without any commitment regarding support. Issues and discussions can be opened, but be prepared to include basic necessary info, so the community is able to help.
<br><br>

## Logs
* Check if your containers are running correctly: `dps`
* Check if your system resources are not exhausted: `htop`, `docker stats`
* Check if there is a port conflict:
```
systemctl stop tpot
grc netstat -tulpen
mi ~/tpotce/docker-compose.yml
docker-compose -f ~/tpotce/docker-compose.yml up
CTRL+C
docker-compose -f ~/tpotce/docker-compose.yml down -v
```
* Check individual container logs: `docker logs -f <container_name>`
* Check `tpotinit` log: `cat ~/tpotce/data/tpotinit.log`
<br><br>

## RAM and Storage
The Elastic Stack is hungry for RAM, specifically `logstash` and `elasticsearch`. If the Elastic Stack is unavailable, does not receive any logs or simply keeps crashing it is most likely a RAM or storage issue.<br>
While T-Pot keeps trying to restart the services / containers run `docker logs -f <container_name>` (either `logstash` or `elasticsearch`) and check if there are any warnings or failures involving RAM.

Storage failures can be identified easier via `htop`. 
<br><br>

# Contact
T-Pot is provided ***as is*** open source ***without*** any commitment regarding support ([see the disclaimer](#disclaimer)).

If you are a security researcher and want to responsibly report an issue please get in touch with our [CERT](https://www.telekom.com/en/corporate-responsibility/data-protection-data-security/security/details/introducing-deutsche-telekom-cert-358316).
<br><br>

## Issues
Please report issues (errors) on our [GitHub Issues](https://github.com/telekom-security/tpotce/issues), but [troubleshoot](#troubleshooting) first. Issues not providing information to address the error will be closed or converted into [discussions](#discussions).

Use the search function first, it is possible a similar issue has been addressed or discussed already, with the solution just a search away.
<br><br>

## Discussions
General questions, ideas, show & tell, etc. can be addressed on our [GitHub Discussions](https://github.com/telekom-security/tpotce/discussions).

Use the search function, it is possible a similar discussion has been opened already, with an answer just a search away.
<br><br>

# Licenses
The software that T-Pot is built on uses the following licenses.
<br>GPLv2:
[conpot](https://github.com/mushorg/conpot/blob/master/LICENSE.txt),
[galah](https://github.com/0x4D31/galah?tab=Apache-2.0-1-ov-file#readme),
[dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE),
[honeytrap](https://github.com/armedpot/honeytrap/blob/master/LICENSE),
[suricata](https://suricata.io/features/open-source/)
<br>GPLv3:
[adbhoney](https://github.com/huuck/ADBHoney),
[elasticpot](https://gitlab.com/bontchev/elasticpot/-/blob/master/LICENSE),
[ewsposter](https://github.com/telekom-security/ews/),
[log4pot](https://github.com/thomaspatzke/Log4Pot/blob/master/LICENSE),
[fatt](https://github.com/0x4D31/fatt/blob/master/LICENSE),
[heralding](https://github.com/johnnykv/heralding/blob/master/LICENSE.txt),
[ipphoney](https://gitlab.com/bontchev/ipphoney/-/blob/master/LICENSE),
[miniprint](https://github.com/sa7mon/miniprint?tab=GPL-3.0-1-ov-file#readme),
[redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot/blob/main/LICENSE),
[sentrypeer](https://github.com/SentryPeer/SentryPeer/blob/main/LICENSE.GPL-3.0-only),
[snare](https://github.com/mushorg/snare/blob/master/LICENSE),
[tanner](https://github.com/mushorg/snare/blob/master/LICENSE)
<br>Apache 2 License:
[cyberchef](https://github.com/gchq/CyberChef/blob/master/LICENSE),
[dicompot](https://github.com/nsmfoo/dicompot/blob/master/LICENSE),
[elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt),
[go-pot](https://github.com/ryanolee/go-pot?tab=License-1-ov-file#readme),
[h0neytr4p](https://github.com/pbssubhash/h0neytr4p?tab=Apache-2.0-1-ov-file#readme),
[logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE),
[kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md),
[docker](https://github.com/docker/docker/blob/master/LICENSE)
<br>MIT license:
[autoheal](https://github.com/willfarrell/docker-autoheal?tab=MIT-1-ov-file#readme),
[beelzebub](https://github.com/mariocandela/beelzebub?tab=MIT-1-ov-file#readme),
[ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/blob/master/LICENSE),
[ddospot](https://github.com/aelth/ddospot/blob/master/LICENSE),
[elasticvue](https://github.com/cars10/elasticvue/blob/master/LICENSE),
[glutton](https://github.com/mushorg/glutton/blob/master/LICENSE),
[hellpot](https://github.com/yunginnanet/HellPot/blob/master/LICENSE),
[honeyaml](https://github.com/mmta/honeyaml?tab=MIT-1-ov-file#readme),
[maltrail](https://github.com/stamparm/maltrail/blob/master/LICENSE)
<br>Unlicense:
[endlessh](https://github.com/skeeto/endlessh/blob/master/UNLICENSE)
<br>Other:
[citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot#licencing-agreement-malwaretech-public-licence),
[cowrie](https://github.com/cowrie/cowrie/blob/master/LICENSE.rst),
[mailoney](https://github.com/awhitehatter/mailoney),
[Elastic License](https://www.elastic.co/licensing/elastic-license),
[Wordpot](https://github.com/gbrindisi/wordpot)
<br>AGPL-3.0:
[honeypots](https://github.com/qeeqbox/honeypots/blob/main/LICENSE)
<br>[Public Domain (CC)](https://creativecommons.org/publicdomain/zero/1.0/):
[Harvard Dataverse](https://dataverse.harvard.edu/dataverse/harvard/?q=dicom) 
<br><br>

# Credits
Without open source and the development community we are proud to be a part of, T-Pot would not have been possible! Our thanks are extended but not limited to the following people and organizations:
<br><br>

## The developers and development communities of

* [adbhoney](https://github.com/huuck/ADBHoney/graphs/contributors),
[beelzebub](https://github.com/mariocandela/beelzebub/graphs/contributors),
[ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/graphs/contributors),
[citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot/graphs/contributors),
[conpot](https://github.com/mushorg/conpot/graphs/contributors),
[cowrie](https://github.com/cowrie/cowrie/graphs/contributors),
[ddospot](https://github.com/aelth/ddospot/graphs/contributors),
[dicompot](https://github.com/nsmfoo/dicompot/graphs/contributors),
[dionaea](https://github.com/DinoTools/dionaea/graphs/contributors),
[docker](https://github.com/docker/docker/graphs/contributors),
[elasticpot](https://gitlab.com/bontchev/elasticpot/-/project_members),
[elasticsearch](https://github.com/elastic/elasticsearch/graphs/contributors),
[elasticvue](https://github.com/cars10/elasticvue/graphs/contributors),
[endlessh](https://github.com/skeeto/endlessh/graphs/contributors),
[ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors),
[fatt](https://github.com/0x4D31/fatt/graphs/contributors),
[galah](https://github.com/0x4D31/galah/graphs/contributors),
[glutton](https://github.com/mushorg/glutton/graphs/contributors),
[go-pot](https://github.com/ryanolee/go-pot/graphs/contributors),
[h0neytr4p](https://github.com/pbssubhash/h0neytr4p/graphs/contributors),
[hellpot](https://github.com/yunginnanet/HellPot/graphs/contributors),
[heralding](https://github.com/johnnykv/heralding/graphs/contributors),
[honeyaml](https://github.com/mmta/honeyaml/graphs/contributors),
[honeypots](https://github.com/qeeqbox/honeypots/graphs/contributors),
[honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors),
[ipphoney](https://gitlab.com/bontchev/ipphoney/-/project_members),
[kibana](https://github.com/elastic/kibana/graphs/contributors),
[logstash](https://github.com/elastic/logstash/graphs/contributors),
[log4pot](https://github.com/thomaspatzke/Log4Pot/graphs/contributors),
[mailoney](https://github.com/awhitehatter/mailoney),
[maltrail](https://github.com/stamparm/maltrail/graphs/contributors),
[medpot](https://github.com/schmalle/medpot/graphs/contributors),
[miniprint](https://github.com/sa7mon/miniprint/graphs/contributors),
[p0f](http://lcamtuf.coredump.cx/p0f3/),
[redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot/graphs/contributors),
[sentrypeer](https://github.com/SentryPeer/SentryPeer/graphs/contributors),
[spiderfoot](https://github.com/smicallef/spiderfoot),
[snare](https://github.com/mushorg/snare/graphs/contributors),
[tanner](https://github.com/mushorg/tanner/graphs/contributors),
[suricata](https://github.com/OISF/suricata/graphs/contributors),
[wordpot](https://github.com/gbrindisi/wordpot)
<br><br>

## **The following companies and organizations**
* [docker](https://www.docker.com/),
[elastic.io](https://www.elastic.co/),
[honeynet project](https://www.honeynet.org/)
<br><br>

## **And of course ***YOU*** for joining the community!**
<br>

# Testimonials
One of the greatest feedback we have gotten so far is by one of the Conpot developers:<br>
***"[...] I highly recommend T-Pot which is ... it's not exactly a swiss army knife .. it's more like a swiss army soldier, equipped with a swiss army knife. Inside a tank. A swiss tank. [...]"***
<br><br>

And from @robcowart (creator of [ElastiFlow](https://github.com/robcowart/elastiflow)):<br>
***"#TPot is one of the most well put together turnkey honeypot solutions. It is a must-have for anyone wanting to analyze and understand the behavior of malicious actors and the threat they pose to your organization."***
<br><br>

# Thank you 💖
