# Changelog

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
