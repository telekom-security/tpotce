# Contribution

Thank you for your decision to contribute to T-Pot.

## Issues

Please feel free to post your problems, ideas and issues [here](https://github.com/dtag-dev-sec/tpotce/issues). We will try to answer ASAP, but to speed things up we encourage you to ...
- [ ] Use the [search function](https://github.com/dtag-dev-sec/tpotce/issues?utf8=%E2%9C%93&q=) first
- [ ] Check the [FAQ](#faq)
- [ ] Provide [basic support information](#info) with regard to your issue

Thank you :smiley:

-

<a name="faq"></a>
### FAQ

##### Where can I find the honeypot logs?
###### The honeypot logs are located in `/data/`. You have to login via ssh and run `sudo cd /data/`. Do not change any permissions here or T-Pot will fail to work.

-


<a name="info"></a>
### Baisc support information

- What T-Pot version are you currtently using?
- Are you running on a Intel NUC or a VM?
- How long has your installation been running?
- Did you install any upgrades or packages?
- Did you modify any scripts?
- Have you turned persistence on/off?
- How much RAM available (login via ssh and run `htop`)?
- How much stress are the CPUs under (login via ssh and run `htop`)?
- How much swap space is being used (login via ssh and run `htop`)?
- How much free disk space is available (login via ssh and run `sudo df -h`)?
- What is the current container status (login via ssh and run `sudo start.sh`)?
