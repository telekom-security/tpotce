# Package names of the personas

apt, yum 3 and opkg of the personas install only names that the release has; a name outside its
list gets the error text of the real package manager. The lists hold names only (no versions, no
descriptions, no dependencies), plus the virtual names of apt and the plain names that yum packages
provide.

| File | Releases | Content |
| --- | --- | --- |
| `apt.txt.gz` | `b` Debian 12 (bookworm), `t` Debian 13 (trixie), `r` Raspberry Pi OS bookworm (arm64), `j` Ubuntu 22.04 (jammy), `n` Ubuntu 24.04 (noble) | `<name> <release letters>` |
| `apt-virtual.txt.gz` | the same letters | `<virtual name> <release letters> <provider,...>` |
| `yum.txt.gz` | CentOS 7.9.2009 (os, updates, extras) | `<name>`, or `<provided name or program> <package,...>` (every provider; the first is the shortest name, derived, not recorded; `yum provides` prints them sorted by name, recorded) |
| `opkg.txt.gz` | `a` OpenWrt 21.02.7, `b` OpenWrt 22.03.7 (mipsel_24kc, ramips/mt7621) | `<name> <release letters> <feed>` |
| `apt-meta.txt.gz` | the letters of `apt.txt.gz` | 13 columns: `<name>\t<release letters>\t<version>\t<component>\t<pocket>\t<installed size>\t<size>\t<all or ->\t<section>\t<maintainer>\t<priority>\t<essential yes or ->\t<short description>` |
| `apt-relations.txt.gz` | the letters of `apt.txt.gz` | `<name>\t<release letters>\t<version>` and one column per `RELATION_FIELDS` of the builder (Depends, Homepage, Tag, ...; a line break as `\n`) of the candidates of `apt-meta.txt.gz` for the packages the personas model, EXTRA, SERVICES and the base system (other names of `apt-meta.txt.gz` print no relation fields); `apt show`, `apt-cache show` and `dpkg -s` print them |
| `deb-base.txt.gz` | the letters of `apt.txt.gz` | `<name> <release letters>`: the base system (Priority required / important and the packages of the official image) |
| `deb-files.txt.gz` | `b`, `t`, `j`, `n` (Raspberry Pi OS takes `b`) | `<path> <release letters> <package>`: the programs in /bin, /sbin, /usr/bin, /usr/sbin of the image's packages |
| `opkg-meta.txt.gz` | the letters of `opkg.txt.gz` | `<name>\t<release letters>\t<version>\t<description>` (the description on the first row of a name) |
| `dnf.txt.gz` | RHEL 9 (the BaseOS and AppStream repositories of AlmaLinux 9.4) | `<name> <baseos or appstream>`, or `<provided name or program> > <package,...>` (every provider; the first is the shortest name, derived, not recorded; `dnf provides` prints them sorted by name, recorded) |
| `yum-meta.txt.gz` | CentOS 7.9.2009 | 12 columns: `<name>\t<arch>\t<epoch:version-release>\t<repo>\t<size>\t<installed size>\t<summary>\t<url>\t<license>\t<group>\t<description, \n escaped; only for the popcon top 3,000, EXTRA and the modelled packages>\t<source rpm>` |
| `dnf-meta.txt.gz` | RHEL 9 (AlmaLinux 9.4 vault) | the columns of `yum-meta.txt.gz`; a build outside the module streams before the builds of a stream (as dnf installs nginx 1.20.1, not nginx:1.24), a name only a stream has with the stream's build |
| `rpm-man.txt.gz` | `c` CentOS 7.9.2009, `r` RHEL 9 (AlmaLinux 9.4 vault) | `<manual page> <release letters> <package>`: the manual pages in sections 1 and 8 named as one of the package's own programs, from the filelists |

A release covers the components and pockets that the persona's own sources name:

- bookworm: main of bookworm, -updates, -security.
- trixie: main of trixie, -updates, -backports, -security.
- Raspberry Pi OS: main, contrib, non-free, non-free-firmware of bookworm, -updates, -security, plus the Raspberry Pi archive.
- jammy: main, restricted, universe; main and restricted of -updates; main, restricted, universe of -security.
- noble: main, restricted, universe, multiverse of noble, -updates, -backports, -security.

Debian main, Ubuntu main and restricted and the Raspberry Pi archive are taken whole. The other indexes keep
the names in the top 20,000 of Debian popcon (`by_inst`) and the names of `EXTRA` in the builder.
Names of a kernel ABI are left out. At start the persona adds the names of its own kernel and the
packages it models (`system.APT_INSTALLED`, `APT_AVAILABLE`, the release's packages). A real name
that ranks lower is not found. Virtual names of libraries and language modules are left out, and
yum takes the provider with the shortest name (unverified: yum's own choice also weighs arch and
repository). yum and opkg keep their whole indexes, but yum has no sonames.

The recorded inputs `deb-files-<letter>.txt` (`dpkg -L` of every package of the image, the paths in
/bin, /sbin, /usr/bin and /usr/sbin) and `deb-base-<letter>.txt` (`dpkg-query -W`) come from the official
images (linux/arm64, 2026-10-15): `b` debian:12 `debian@sha256:2c037a04925515fdd6ea85ea14a682d0e79931f5e9f5d07b6dbfc6ba12f9e858`,
`t` debian:13 `debian@sha256:913f6706df59a68922d1dd08f78c2476560a8d367897200a6005b00e5f67c2d5`, `j` ubuntu:22.04
`ubuntu@sha256:5ec03bb3441e8b0bf3b4f9cd4629a1ae763010dc3035bb8da3ae6cf026486401`, `n` ubuntu:24.04
`ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55`. Debian 12 and Ubuntu 22.04
record /bin/ls, Debian 13 and Ubuntu 24.04 /usr/bin/ls (as `dpkg -S` finds them there).

`apt-meta.txt.gz` keeps, per release, the candidate of the names in the top 2,000 of popcon, of `EXTRA`,
of `SERVICES` (the common service packages: nginx, apache2, mariadb-server, postgresql, redis-server, docker.io,
postfix and the tools bots install; they rank below the popcon limit) and of the packages the personas model: the highest version of the release and its -updates and
-security pockets (-backports only for a name without another candidate; of equal versions the first
pocket of the sources), its component, sizes and short description. A name without a row installs
with a version that fits the release (`apt_fallback`). `opkg-meta.txt.gz` keeps every package of the
feeds with its version and description. `dnf.txt.gz` takes the names of AlmaLinux 9.4 for those of
RHEL 9: the AlmaLinux brand packages are named as those of Red Hat (`REBRAND` in the builder), and
the programs in /usr/bin and /usr/sbin are names dnf installs by (`/usr/bin/mail` gives s-nail).

## Sources (pinned)

The inputs of the builder are the constant `SOURCES` in `tools/build_package_names.py`: each file of the
download folder with its URL and its sha256. A file is only taken when its hash is the pinned one. The lists
were built from the files of 2026-10-13 to 15; the URLs name the archive of that day or a release that does not
change:

| File | URL |
| --- | --- |
| `deb-<suite>-<component>-<arch>.xz`, `deb-<suite>-updates-...`, `deb-trixie-backports-...` | https://snapshot.debian.org/archive/debian/20261013T143410Z/dists/<suite>/<component>/binary-<arch>/Packages.xz |
| `deb-<suite>-security-<component>-<arch>.xz` | https://snapshot.debian.org/archive/debian-security/20261013T073402Z/dists/<suite>-security/<component>/binary-<arch>/Packages.xz |
| `ub-<suite>[-pocket]-<component>-amd64.xz` | https://snapshot.ubuntu.com/ubuntu/20261013T160000Z/dists/<suite>[-pocket]/<component>/binary-amd64/Packages.xz (suites noble, jammy; pockets updates, backports, security) |
| `c7-<repo>-primary.xml.gz`, `c7-<repo>-filelists.xml.gz` | https://vault.centos.org/7.9.2009/<repo>/x86_64/repodata/<sha256>-primary.xml.gz (the file is named by its own sha256; repos os, updates, extras) |
| `el94-<repo>-primary.xml.gz`, `el94-<repo>-filelists.xml.gz` | https://vault.almalinux.org/9.4/<repo>/x86_64/os/repodata/<sha256>-primary.xml.gz (repos BaseOS, AppStream) |
| `ow-<version>-core.gz` | https://downloads.openwrt.org/releases/<version>/targets/ramips/mt7621/packages/Packages.gz (versions 21.02.7, 22.03.7) |
| `ow-<version>-<feed>.gz` | https://downloads.openwrt.org/releases/<version>/packages/mipsel_24kc/<feed>/Packages.gz (base, luci, packages, routing, telephony) |
| `popcon-by_inst.gz` | https://popcon.debian.org/by_inst.gz (moves, see below) |
| `rpi-bookworm-main-arm64.gz` | https://archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz (moves, see below) |

The timestamps of the snapshots are the first time at which every pinned file of the archive matched the hash
of the build (the Release files of the snapshot hold the sha256 of each index). `popcon-by_inst.gz` changes
every day and the Raspberry Pi archive keeps no old state: the pin is the hash of the build of the lists, a
newer file is refused, and `--accept-moved` takes it anyway (the names near the popcon limits of
`apt.txt.gz` and `apt-meta.txt.gz` and the Raspberry Pi list then differ from the checked-in ones). It needs an
OUT folder (`--fetch --accept-moved DOWNLOADS OUT`): the builder never overwrites the checked-in lists with
those of a moved file. The
recorded inputs `deb-base-<letter>.txt` and `deb-files-<letter>.txt` (above) cannot be downloaded: put them
into the folder yourself.

Limits of the reproduction (2026-10-22 rebuild from the pinned files). The recordings `deb-base-<letter>.txt` and
`deb-files-<letter>.txt` of the official images are gone and cannot be downloaded; for the rebuild they were made
again from the checked-in `deb-base.txt.gz` and `deb-files.txt.gz`, so those two lists are only trivially equal
(`build_package_installs.py` has the same limit: its recording is gone, the checked-in file is the record). With
today's popcon and Raspberry Pi index the rebuild gave the same rows as the checked-in lists for `opkg`,
`opkg-meta`, `yum`, `yum-meta`, `dnf`, `dnf-meta` and `rpm-man`. `apt`, `apt-virtual` and `apt-meta` differ in the
names that rank about 19,000 to 20,100 (and about 2,000 for `apt-meta`) in the popcon of that day and in the NVIDIA
providers of the Raspberry Pi index (`apt-virtual`); the popcon of 2026-10-13 is not available any more.

Open for the next regeneration: the rows of the `SERVICES` names were taken over from a rebuild with a moved popcon
(28 rows added, none removed); the other rows that rebuild changed (five names left out, four added) were not taken
over. A regeneration needs the recordings and a popcon of one day to be kept with the lists.

Rebuild the lists:

```
python3 -I docker/cowrie/tools/build_package_names.py --fetch      # download what is missing, check each sha256, build
python3 -I docker/cowrie/tools/build_package_names.py --offline    # the cache only (the default)
python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS    # a folder of your own, read as it is
```

The downloads go to `~/.cache/tpot-cowrie/apt/` (`$XDG_CACHE_HOME/tpot-cowrie/apt/`; `--cache DIR` names
another folder), about 170 MB, never into the repository. The builder only reads the names, the Provides, the
listed programs and the fields of apt-meta / opkg-meta; it runs nothing from the indexes.
