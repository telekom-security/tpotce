# Changelog

## 20190404
- **Fix #332**
  - If T-Pot, opposed to the requirements, does not have full internet access netselect-apt fails to determine the fastest mirror as it needs ICMP and UDP outgoing. Should netselect-apt fail the default mirrors will be used.
- **Improve install speed with apt-fast**
  - Migrating from a stable base install to Debian (Sid) requires downloading lots of packages. Depending on your geo location the download speed was already improved by introducing netselect-apt to determine the fastest mirror. With apt-fast the downloads will be even faster by downloading packages not only in parallel but also with multiple connections per package.
  
## 20190416
- **Reapply custom HPFEED settings after update**
  - The `/opt/tpot/update.sh` script overwrites all local changes in `/opt/tpot/etc/tpot.yml`
  - After pulling the latest files from GitHub, check in our backup whether the HPFEED settings were enabled before
  - If they were enabled, the old HPFEED settings are extracted and reapplied to our new tpot.yml
