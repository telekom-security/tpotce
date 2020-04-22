---
name: Bug report for T-Pot
about: Bug report for T-Pot
title: ''
labels: ''
assignees: ''

---

Before you post your issue make sure it has not been answered yet and provide `basic support information` if you come to the conclusion it is a new issue.

- 🔍 Use the [search function](https://github.com/dtag-dev-sec/tpotce/issues?utf8=%E2%9C%93&q=) first
- 🧐 Check our [WIKI](https://github.com/dtag-dev-sec/tpotce/wiki)
- 📚 Consult the documentation of 💻 [Debian](https://www.debian.org/doc/), 🐳 [Docker](https://docs.docker.com/), the 🦌 [ELK stack](https://www.elastic.co/guide/index.html) and the 🍯 [T-Pot Readme](https://github.com/dtag-dev-sec/tpotce/blob/master/README.md).
- **⚠️ Provide [basic support information](#info) or similiar information with regard to your issue or we can not help you and will close the issue without further notice**

<br>
<br>
<br>

<a name="info"></a>
## ⚠️ Basic support information (commands are expected to run as `root`)

- What version of the OS are you currently using `lsb_release -a` and `uname -a`?
- What T-Pot version are you currently using?
- What edition (Standard, Nextgen, etc.) of T-Pot are you running?
- What architecture are you running on (i.e. hardware, cloud, VM, etc.)?
- Did you have any problems during the install? If yes, please attach `/install.log` `/install.err`.
- How long has your installation been running?
- Did you install upgrades, packages or use the update script?
- Did you modify any scripts or configs? If yes, please attach the changes.
- Please provide a screenshot of `glances` and `htop`.
- How much free disk space is available (`df -h`)?
- What is the current container status (`dps.sh`)?
- What is the status of the T-Pot service (`systemctl status tpot`)?
- What ports are being occupied? Stop T-Pot `systemctl stop tpot` and run `netstat -tulpen`
- If a single container shows as `DOWN` you can run `docker logs <container-name>` for the latest log entries
