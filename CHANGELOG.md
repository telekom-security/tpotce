# Changelog

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
