# Changelog

## 20200630
- **Release T-Pot 20.06**
  - After 4 months of public testing with the NextGen edition T-Pot 20.06 can finally be released.
- **Debian Buster**
  - With the release of Debian Buster T-Pot now has access to all packages required right out of the box.
- **Add new honeypots**
  - [Dicompot](https://github.com/nsmfoo/dicompot) by @nsmfoo is a low interaction honeypot for the Dicom protocol which is the international standard to process medical imaging information. Together with Medpot which supports the HL7 protocol T-Pot is now offering a Medical Installation type.
  - [Honeysap](https://github.com/SecureAuthCorp/HoneySAP) by SecureAuthCorp is a low interaction honeypot for the SAP services, in case of T-Pot configured for the SAP router.
  - [Elasticpot](https://gitlab.com/bontchev/elasticpot) by Vesselin Bontchev replaces ElasticpotPY as a low interaction honeypot for Elasticsearch with more features, plugins and scripted responses.
- **Rebuild Images**
  - All docker images were rebuilt based on the latest (and stable running) versions of the tools and honeypots. Mostly the images now run on Alpine 3.12 / Debian Buster. However some honeypots / tools still reuire Alpine 3.11 / 3.10 to run properly.
- **Install Types**
  - All docker-compose files (`/opt/tpot/etc/compose`) were remixed and most of the NextGen honeypots are now available in Standard.
  - There is now a **Medical** Installation Type with Dicompot and Medpot which will be of most interest for medical institutions to get started with T-Pot.
- **Update Tools**
  - Connecting to T-Pot via `https://<ip>:64297` brings you to the T-Pot Landing Page now which is based on Heimdall and the latest NGINX enforcing TLS 1.3.
  - The ELK stack was updated to 7.8.0 and stripped down to the necessary core functions (where possible) for T-Pot while keeping ELK RAM requirements to a minimum (8GB of RAM is recommended now). The number of index pattern fields was reduced to **697** which increases performance significantly. There are **22** Kibana Dashboards, **397** Kibana Visualizations and **24** Kibana Searches readily available to cover all your needs to get started and familiar with T-Pot.
  - Cyberchef was updated to 9.21.0.
  - Elasticsearch Head was updated to the latest version available on GitHub.
  - Spiderfoot was updated to latest 3.1 dev.
- **Landing Page**
  - After logging into T-Pot via web you are now greeted with a beautifully designed landing page.
- **Countless Tweaks and improvements**
  - Under the hood lots of tiny tweaks, improvements and a few bugfixes will increase your overall experience with T-Pot.

## 20200316
- **Move from Sid to Stable**
  - Debian Stable has now all the packages and versions we need for T-Pot. As a consequence we can now move to the `stable` branch.

## 20200310
- **Add 2FA to Cockpit**
  - Just run `2fa.sh` to enable two factor authentication in Cockpit.
- **Find fastest mirror with netselect-apt**
  - Netselect-apt will find the fastest mirror close to you (outgoing ICMP required).

## 20200309
- **Bump Nextgen to 20.06**
  - All NextGen images have been rebuilt to their latest master.
  - ElasticStack bumped to 7.6.1 (Elasticsearch will need at least 2048MB of RAM now, T-Pot at least 8GB of RAM) and tweak to accomodate changes of 7.x.
  - Fixed errors in Tanner / Snare which will now handle downloads of malware via SSL and store them correctly (thanks to @afeena).
  - Fixed errors in Heralding which will now improve on RDP connections (thanks to @johnnykv, @realsdx).
  - Fixed error in honeytrap which will now build in Debian/Buster (thanks to @tillmannw).
  - Mailoney is now logging in JSON format (thanks to @monsherko).
  - Base T-Pot landing page on Heimdall.
  - Tweaking of tools and some minor bug fixing

## 20200116
- **Bump ELK to latest 6.8.6**
- **Update ISO image to fix upstream bug of missing kernel modules**
- **Include dashboards for CitrixHoneypot**
  - Please run `/opt/tpot/update.sh` for the necessary modifications, omit the reboot and run `/opt/tpot/bin/tped.sh` to (re-)select the NextGen installation type.
  - This update requires the latest Kibana objects as well. Download the latest from https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/etc/objects/kibana_export.json.zip, unzip and import the objects within Kibana WebUI > Management > Saved Objects > Export / Import". All objects will be overwritten upon import, make sure to run an export first.

## 20200115
- **Prepare integration of CitrixHoneypot**
  - Prepare integration of [CitrixHoneypot](https://github.com/MalwareTech/CitrixHoneypot) by MalwareTech
  - Integration into ELK is still open
  - Please run `/opt/tpot/update.sh` for the necessary modifications, omit the reboot and run `/opt/tpot/bin/tped.sh` to (re-)select the NextGen installation type.

## 20191224
- **Use pigz, optimize logrotate.conf**
  - Use `pigz` for faster archiving, especially with regard to high volumes of logs - Thanks to @workandresearchgithub!
  - Optimize `logrotate.conf` to improve archiving speed and get rid of multiple compression, also introduce `pigz`.

## 20191121
- **Bump ADBHoney to latest master**
  - Use latest version of ADBHoney, which now fully support Python 3.x - Thanks to @huuck!

## 20191113, 20191104, 20191103, 20191028
- **Switch to Debian 10 on OTC, Ansible Improvements**
  - OTC now supporting Debian 10 - Thanks to @shaderecker!

## 20191028
- **Fix an issue with pip3, yq**
  - `yq` needs rehashing.

## 20191026
- **Remove cockpit-pcp**
  - `cockpit-pcp` floods swap for some reason - removing for now.

## 20191022
- **Bump Suricata to 5.0.0**

## 20191021
- **Bump Cowrie to 2.0.0**

## 20191016
- **Tweak installer, pip3, Heralding**
  - Install `cockpit-pcp` right from the start for machine monitoring in cockpit.
  - Move installer and update script to use pip3.
  - Bump heralding to latest master (1.0.6) - Thanks @johnnykv!

## 20191015
- **Tweaking, Bump glutton, unlock ES script**
  - Add `unlock.sh` to unlock ES indices in case of lockdown after disk quota has been reached.
  - Prevent too much terminal logging from p0f and glutton since `daemon.log` was filled up.
  - Bump glutton to latest master now supporting payload_hex. Thanks to @glaslos.

## 20191002
- **Merge**
  - Support Debian Buster images for AWS #454
  - Thank you @piffey

## 20190924
- **Bump EWSPoster**
  - Supports Python 3.x
  - Thank you @Trixam

## 20190919
- **Merge**
  - Handle non-interactive shells #454
  - Thank you @Oogy

## 20190907
- **Logo tweaking**
  - Add QR logo

## 20190828
- **Upgrades and rebuilds**
  - Bump Medpot, Nginx and Adbhoney to latest master
  - Bump ELK stack to 6.8.2
  - Rebuild Mailoney, Honeytrap, Elasticpot and Ciscoasa
  - Add 1080p T-Pot wallpaper for download

## 20190824
- **Add some logo work**
  - Thanks to @thehadilps's suggestion adjusted social preview
  - Added 4k T-Pot wallpaper for download

## 20190823
- **Fix for broken Fuse package**
  - Fuse package in upstream is broken
  - Adjust installer as workaround, fixes #442

## 20190816
- **Upgrades and rebuilds**
  - Adjust Dionaea to avoid nmap detection, fixes #435 (thanks @iukea1)
  - Bump Tanner, Cyberchef, Spiderfoot and ES Head to latest master

## 20190815
- **Bump ELK stack to 6.7.2**
  - Transition to 7.x must iterate slowly through previous versions to prevent changes breaking T-Pots

## 20190814
- **Logstash Translation Maps improvement**
  - Download translation maps rather than running a git pull
  - Translation maps will now be bzip2 compressed to reduce traffic to a minimum
  - Fixes #432

## 20190802
- **Add support for Buster as base image**
  - Install ISO is now based on Debian Buster
  - Installation upon Debian Buster is now supported

## 20190701
- **Reworked Ansible T-Pot Deployment**
  - Transitioned from bash script to all Ansible
  - Reusable Ansible Playbook for OpenStack clouds
  - Example Showcase with our Open Telekom Cloud
  - Adaptable for other cloud providers

## 20190626
- **HPFEEDS Opt-In commandline option**
  - Pass a hpfeeds config file as a commandline argument
  - hpfeeds config is saved in `/data/ews/conf/hpfeeds.cfg`
  - Update script restores hpfeeds config

## 20190604
- **Finalize Fatt support**
  - Build visualizations, searches, dashboards
  - Rebuild index patterns
  - Some finishing touches

## 20190601
- **Start supporting Fatt, remove Glastopf**
  - Build Dockerfile, Adjust logstash, installer, update and such.
  - Glastopf is no longer supported within T-Pot

## 20190528+20190531
- **Increase total number of fields**
  - Adjust total number of fileds for logstash templae from 1000 to 2000.

## 20190526
- **Fix build for Cowrie**
  - Upstream changes required a new package `py-bcrypt`.

## 20190525
- **Fix build for RDPY**
  - Building was prevented due to cache error which occurs lately on Alpine if `apk` is using `--no-ache' as options.

## 20190520
- **Adjust permissions for /data folder**
  - Now it is possible to download files from `/data` using SCP, WINSCP or CyberDuck.

## 20190513
- **Added Ansible T-Pot Deployment on Open Telekom Cloud**
  - Reusable Ansible Playbooks for all cloud providers
  - Example Showcase with our Open Telekom Cloud

## 20190511
- **Add hptest script**
  - Quickly test if the honeypots are working with `hptest.sh <[ip,host]>` based on nmap.

## 20190508
- **Add tsec / install user to tpot group**
  - For users being able to easily download logs from the /data folder the installer now adds the `tpot` or the logged in user (`who am i`) via `usermod -a -G tpot <user>` to the tpot group. Also /data permissions will now be enforced to `770`, which is necessary for directory listings.

## 20190502
- **Fix KVPs**
  - Some KVPs for Cowrie changed and the tagcloud was not showing any values in the Cowrie dashboard.
  - New installations are not affected, however existing installations need to import the objects from /opt/tpot/etc/objects/kibana-objects.json.zip.
- **Makeiso**
  - Move to Xorriso for building the ISO image.
  - This allows to support most of the Debian based distros, i.e. Debian, MxLinux and Ubuntu.

## 20190428
- **Rebuild ISO**
  - The install ISO needed a rebuilt after some changes in the Debian mirrors.
- **Disable Netselect**
  - After some reports in the issues that some Debian mirrors were not fully synced and thus some packages were unavailable the netselect-apt feature was disabled.

## 20190406
- **Fix for SSH**
  - In some situations the SSH Port was not written to a new line (thanks to @dpisano for reporting).
- **Fix race condition for apt-fast**
  - Curl and wget need to be installed before apt-fast installation.

## 20190404
- **Fix #332**
  - If T-Pot, opposed to the requirements, does not have full internet access netselect-apt fails to determine the fastest mirror as it needs ICMP and UDP outgoing. Should netselect-apt fail the default mirrors will be used.
- **Improve install speed with apt-fast**
  - Migrating from a stable base install to Debian (Sid) requires downloading lots of packages. Depending on your geo location the download speed was already improved by introducing netselect-apt to determine the fastest mirror. With apt-fast the downloads will be even faster by downloading packages not only in parallel but also with multiple connections per package.

`git log --date=format:"## %Y%m%d" --pretty=format:"%ad %n- **%s**%n  - %b"`
