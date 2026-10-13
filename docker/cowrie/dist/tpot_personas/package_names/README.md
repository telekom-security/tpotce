# Package names of the personas

apt, yum 3 and opkg of the personas install only names that the release has. A name outside its
list gets the error text of the real package manager. The lists hold names only: no versions, no
descriptions, no dependencies.

| File | Releases | Content |
| --- | --- | --- |
| `apt.txt.gz` | `b` Debian 12 (bookworm), `t` Debian 13 (trixie), `r` Raspberry Pi OS bookworm (arm64), `j` Ubuntu 22.04 (jammy), `n` Ubuntu 24.04 (noble) | `<name> <release letters>` |
| `yum.txt.gz` | CentOS 7.9.2009 (os, updates, extras) | `<name>` |
| `opkg.txt.gz` | `a` OpenWrt 21.02.7, `b` OpenWrt 22.03.7 (mipsel_24kc, ramips/mt7621) | `<name> <release letters> <feed>` |

The full indexes of Debian and Ubuntu hold about 65,000 to 70,000 names per release, so the apt
list is cut. A release keeps the names of its index that rank in the top 20,000 of Debian popcon
(`by_inst`). It also keeps every name of Ubuntu main and of the Raspberry Pi archive, plus the
names of `EXTRA` in the builder where the release has them. At start the persona adds the packages
it models (`system.APT_INSTALLED`, `APT_AVAILABLE`, the release's packages). A real name that ranks
lower is not found. yum and opkg keep their whole indexes.

## Sources (downloaded 2026-10-13)

The download folder of the builder holds these files under these names:

| File | URL |
| --- | --- |
| `deb-bookworm.xz` | http://deb.debian.org/debian/dists/bookworm/main/binary-amd64/Packages.xz |
| `deb-trixie.xz` | http://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz |
| `deb-bookworm-arm64.xz` | http://deb.debian.org/debian/dists/bookworm/main/binary-arm64/Packages.xz |
| `deb-bookworm-nff-arm64.xz` | http://deb.debian.org/debian/dists/bookworm/non-free-firmware/binary-arm64/Packages.xz |
| `rpi-bookworm-arm64.gz` | http://archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz |
| `ub-jammy-main.xz`, `ub-jammy-universe.xz` | http://archive.ubuntu.com/ubuntu/dists/jammy/{main,universe}/binary-amd64/Packages.xz |
| `ub-noble-main.xz`, `ub-noble-universe.xz` | http://archive.ubuntu.com/ubuntu/dists/noble/{main,universe}/binary-amd64/Packages.xz |
| `popcon-by_inst.gz` | https://popcon.debian.org/by_inst.gz |
| `c7-{os,updates,extras}-primary.xml.gz` | https://vault.centos.org/7.9.2009/{os,updates,extras}/x86_64/repodata/ (the `primary.xml.gz` that `repomd.xml` names) |
| `ow-<version>-core.gz` | https://downloads.openwrt.org/releases/<version>/targets/ramips/mt7621/packages/Packages.gz |
| `ow-<version>-<feed>.gz` | https://downloads.openwrt.org/releases/<version>/packages/mipsel_24kc/<feed>/Packages.gz (base, luci, packages, routing, telephony) |

Rebuild the lists with `python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS`. The
builder only reads the names and runs nothing from the indexes.
