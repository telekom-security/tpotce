---
name: Bug report for T-Pot 24.04.x
about: Bug report for T-Pot 24.04.x
title: ''
labels: ''
assignees: ''

---

# Ask T-Pot Assistant
- 🤖 Ask [T-Pot Assistant (beta)](https://chatgpt.com/g/g-67OJ5idsQ-t-pot-assistant-beta) if you have not read the documentation yet and do not intent to do so (#1564)

# Successfully raise an issue
Before you post your issue make sure it has not been answered yet and provide **⚠️ BASIC SUPPORT INFORMATION** (as requested below) if you come to the conclusion it is a new issue.

- 🔍 Use the [search function](https://github.com/dtag-dev-sec/tpotce/issues?utf8=%E2%9C%93&q=) first
- 🧐 Check our [Wiki](https://github.com/dtag-dev-sec/tpotce/wiki) and the [discussions](https://github.com/telekom-security/tpotce/discussions)
- 📚 Consult the documentation of 💻 your Linux OS, 🐳 [Docker](https://docs.docker.com/), the 🦌 [Elastic stack](https://www.elastic.co/guide/index.html) and the 🍯 [T-Pot Readme](https://github.com/dtag-dev-sec/tpotce/blob/master/README.md).
- ⚙️ The [Troubleshoot Section](https://github.com/telekom-security/tpotce?tab=readme-ov-file#troubleshooting) of the [T-Pot Readme](https://github.com/dtag-dev-sec/tpotce/blob/master/README.md) is a good starting point to collect a good set of information for the issue and / or to fix things on your own.
- **⚠️ Provide [BASIC SUPPORT INFORMATION](#-basic-support-information-commands-are-expected-to-run-as-root) or similar detailed information with regard to your issue or we will close the issue or convert it into a discussion without further interaction from the maintainers**.<br>

# ⚠️ Basic support information (commands are expected to run as `root`)

**We happily take the time to improve T-Pot and take care of things, but we need you to take the time to create an issue that provides us with all the information we need.** 

- What OS are you T-Pot running on?
- What is the version of the OS `lsb_release -a` and `uname -a`?
- What T-Pot version are you currently using (only **T-Pot 24.04.x** is currently supported)?
- What architecture are you running on (i.e. hardware, cloud, VM, etc.)?
- Review the `~/install_tpot.log`, attach the log and highlight the errors.
- How long has your installation been running?
  - If it is a fresh install consult the documentation first.
  - Most likely it is a port conflict or a remote dependency was unavailable.
  - Retry a fresh installation and only open the issue if the error keeps coming up and is not resolved using the documentation as described [here](#how-to-raise-an-issue).  
- Did you install upgrades, packages or use the update script?
- Did you modify any scripts or configs? If yes, please attach the changes.
- Please provide a screenshot of `htop` and `docker stats`.
- How much free disk space is available (`df -h`)?
- What is the current container status (`dps`)?
- On Linux: What is the status of the T-Pot service (`systemctl status tpot`)?
- What ports are being occupied? Stop T-Pot `systemctl stop tpot` and run `grc netstat -tulpen`
  - Stop T-Pot `systemctl stop tpot`
  - Run `grc netstat -tulpen`
  - Run T-Pot manually with `docker compose -f ~/tpotce/docker-compose.yml up` and check for errors
  - Stop execution with `CTRL-C` and `docker compose -f ~/tpotce/docker-compose.yml down -v`
- If a single container shows as `DOWN` you can run `docker logs <container-name>` for the latest log entries
