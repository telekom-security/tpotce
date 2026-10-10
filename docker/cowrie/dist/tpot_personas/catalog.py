"""The persona catalog as data. Each persona has fixed traits (family, user,
architecture, package managers, SSH profile) and pools (releases, hardware,
host names) from which every start draws one Identity.

The values of the 11 personas below are those of the generator before the
persona library (one release, one hardware entry and one host name each).
"""
from . import PersonaError
from .devices import build_iot_nas, build_iot_router, build_openwrt, build_qnap, build_synology, build_ubiquiti
from .model import Hardware, Persona, Release
from .servers import build_debian, build_fedora, build_rhel, build_ubuntu

APT = ("apt", "apt-get", "dpkg")
DNF = ("dnf", "yum", "rpm")

# x86 cloud VM, 2 vCPU / 1 GB (/dev/shm in df is half of MemTotal)
KVM_SMALL = Hardware(cpus=2, cpu_model="Common KVM processor", mem_kb=1017808, disk_kb=20509264)
# single core MIPS SoC with 64 MB as on small routers
MIPS_ROUTER = Hardware(cpus=1, cpu_model="MIPS 24Kc V7.4", mem_kb=65536, disk_kb=8192)


PERSONAS = (
    Persona(
        id="ubuntu-jammy", family="ubuntu", kind="server", os_id="ubuntu",
        user="ubuntu", uid=1000, gid=1000, shell="/bin/bash",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=APT,
        releases=(
            Release(
                label="22.04.4",
                ssh_banner="SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.10",
                ssh_version="OpenSSH_8.9p1, OpenSSL 3.0.2 15 Mar 2022",
                kernel_version="5.15.0-23-generic-amd64",
                kernel_build="#25~22.04-Ubuntu SMP",
                os_version="22.04.4 LTS",
                os_version_id="22.04",
                packages={"apt": "2.4.5", "dpkg": "1.21.1ubuntu2.3",
                          "openssh-client": "1:8.9p1-3ubuntu0.10", "openssh-server": "1:8.9p1-3ubuntu0.10",
                          "curl": "7.81.0-1ubuntu1.15", "git": "1:2.34.1-1ubuntu1.10",
                          "htop": "3.0.5-7build2", "vim": "2:8.2.3995-1ubuntu2.15",
                          "net-tools": "1.60+git20181103.0eebece-1ubuntu5"},
                extra={"codename": "jammy", "codename_long": "Jammy Jellyfish", "debian_version": "bookworm/sid",
                       "kernel_builder": "buildd@lcy02-amd64-028",
                       "upgrades": {"openssh-client": "1:8.9p1-3ubuntu0.6",
                                    "openssh-server": "1:8.9p1-3ubuntu0.6"}},
            ),
        ),
        hardware=(Hardware(cpus=2, cpu_model="Intel(R) Xeon(R) CPU E5-2676 v3 @ 2.40GHz",
                           mem_kb=1017360, disk_kb=20509264),),
        hostnames=("srv01",),
        builder=build_ubuntu,
        vulnerability="Ubuntu 22.04 Jammy OpenSSH 8.9p1 server fingerprint",
    ),
    Persona(
        id="debian-bookworm-vuln", family="debian", kind="server", os_id="debian",
        user="debian", uid=1000, gid=1000, shell="/bin/bash",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=APT,
        releases=(
            Release(
                label="12.5",
                ssh_banner="SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2",
                ssh_version="OpenSSH_9.2p1, OpenSSL 3.0.11 19 Sep 2023",
                kernel_version="6.1.0-18-amd64",
                kernel_build="#1 SMP PREEMPT_DYNAMIC Debian 6.1.76-1",
                os_version="12 (bookworm)",
                os_version_id="12",
                packages={"apt": "2.6.1", "dpkg": "1.21.22",
                          "openssh-client": "1:9.2p1-2+deb12u2", "openssh-server": "1:9.2p1-2+deb12u2",
                          "curl": "7.88.1-10+deb12u5", "git": "1:2.39.2-1.1", "htop": "3.2.2-2",
                          "vim": "2:9.0.1378-2", "sudo": "1.9.13p3-1+deb12u1"},
                extra={"codename": "bookworm", "debian_version": "12.5",
                       "kernel_builder": "debian-kernel@lists.debian.org",
                       "upgrades": {"openssh-client": "1:9.2p1-2+deb12u1",
                                    "openssh-server": "1:9.2p1-2+deb12u1"}},
            ),
        ),
        hardware=(KVM_SMALL,),
        hostnames=("db12-web01",),
        builder=build_debian,
        vulnerability="regreSSHion-era OpenSSH 9.2p1 fingerprint",
    ),
    Persona(
        id="fedora-36-vuln", family="fedora", kind="server", os_id="fedora",
        user="fedora", uid=1000, gid=1000, shell="/bin/bash",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=DNF,
        releases=(
            Release(
                label="36",
                ssh_banner="SSH-2.0-OpenSSH_8.8",
                ssh_version="OpenSSH_8.8p1, OpenSSL 3.0.2 15 Mar 2022",
                kernel_version="5.17.5-300.fc36.x86_64",
                kernel_build="#1 SMP PREEMPT Thu Apr 28 15:57:21 UTC 2022",
                os_version="36 (Server Edition)",
                os_version_id="36",
                packages={"dnf": "4.14.0", "rpm": "4.17.0",
                          "openssh-server": "8.8p1-1.fc36", "curl": "7.82.0-12.fc36",
                          "git": "2.37.3-1.fc36", "htop": "3.2.1-1.fc36", "vim-enhanced": "9.0.475-1.fc36"},
                extra={"codename": "Thirty Six", "variant": "Server Edition",
                       "kernel_builder": "mockbuild@bkernel01.iad2.fedoraproject.org"},
            ),
        ),
        hardware=(KVM_SMALL,),
        hostnames=("fedora-edge",),
        builder=build_fedora,
        vulnerability="EOL Fedora 36 OpenSSH 8.8p1 fingerprint",
    ),
    Persona(
        id="rhel-9-vuln", family="rhel", kind="server", os_id="rhel",
        user="cloud-user", uid=1000, gid=1000, shell="/bin/bash",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=DNF,
        releases=(
            Release(
                label="9.3",
                ssh_banner="SSH-2.0-OpenSSH_8.7",
                ssh_version="OpenSSH_8.7p1, OpenSSL 3.0.7 1 Nov 2022",
                kernel_version="5.14.0-362.8.1.el9_3.x86_64",
                kernel_build="#1 SMP PREEMPT_DYNAMIC Red Hat 5.14.0-362.8.1.el9_3",
                os_version="9.3 (Plow)",
                os_version_id="9.3",
                packages={"dnf": "4.14.0", "rpm": "4.16.1.3",
                          "openssh-server": "8.7p1-34.el9", "curl": "7.76.1-26.el9_3.2",
                          "git": "2.39.3-1.el9_2", "tmux": "3.2a-5.el9", "vim-enhanced": "8.2.2637-20.el9_1"},
                extra={"kernel_builder": "mockbuild@x86-vm-07.build.eng.bos.redhat.com",
                       "upgrades": {"openssh-server": "8.7p1-30.el9_2"}},
            ),
        ),
        hardware=(KVM_SMALL,),
        hostnames=("rhel9-app01",),
        builder=build_rhel,
        vulnerability="RHEL 9 OpenSSH 8.7p1 fingerprint",
    ),
    Persona(
        id="dlink-dir859", family="iot-router", kind="device", os_id="dlink",
        user="admin", uid=1000, gid=1000, shell="/bin/sh",
        arch="linux-mips-lsb", hardware_platform="mips", operating_system="Linux",
        ssh_profile="dropbear", package_managers=(),
        releases=(
            Release(
                label="1.06B01",
                ssh_banner="SSH-2.0-dropbear_2012.55",
                ssh_version="Dropbear sshd 2012.55",
                kernel_version="2.6.30.9",
                kernel_build="#1 Thu Oct 18 17:14:26 CST 2018",
                os_version="V1.06B01",
                os_version_id="1.06",
                extra={"model": "DIR-859", "busybox": "BusyBox v1.19.4 (2018-10-18 17:14:26 CST)",
                       "kernel_builder": "build@dlink-sw2"},
            ),
        ),
        hardware=(MIPS_ROUTER,),
        hostnames=("DIR-859",),
        builder=build_iot_router,
        vulnerability="D-Link DIR-859 CVE-2019-17621-style router fingerprint",
    ),
    Persona(
        id="tplink-wr841n", family="iot-router", kind="device", os_id="tplink",
        user="admin", uid=1000, gid=1000, shell="/bin/sh",
        arch="linux-mips-lsb", hardware_platform="mips", operating_system="Linux",
        ssh_profile="dropbear", package_managers=(),
        releases=(
            Release(
                label="0.9.1",
                ssh_banner="SSH-2.0-dropbear_2015.67",
                ssh_version="Dropbear sshd 2015.67",
                kernel_version="3.18.23",
                kernel_build="#1 Mon Jan 9 15:21:00 CST 2023",
                os_version="0.9.1",
                os_version_id="0.9.1",
                extra={"model": "TL-WR841N", "hw_version": "14.0",
                       "busybox": "BusyBox v1.25.1 (2023-01-09 15:21:00 CST)",
                       "kernel_builder": "tplink@buildserver"},
            ),
        ),
        hardware=(MIPS_ROUTER,),
        hostnames=("TL-WR841N",),
        builder=build_iot_router,
        vulnerability="TP-Link TL-WR841N CVE-2023-33538-style router fingerprint",
    ),
    Persona(
        id="zyxel-nas326", family="iot-nas", kind="device", os_id="zyxel",
        user="admin", uid=1000, gid=1000, shell="/bin/sh",
        arch="linux-arm-lsb", hardware_platform="armv7l", operating_system="Linux",
        ssh_profile="dropbear", package_managers=(),
        releases=(
            Release(
                label="5.21(AAZF.7)",
                ssh_banner="SSH-2.0-dropbear_2014.63",
                ssh_version="Dropbear sshd 2014.63",
                kernel_version="3.2.54",
                kernel_build="#1 SMP Tue Jan 14 02:32:09 CST 2020",
                os_version="V5.21(AAZF.7)",
                os_version_id="5.21",
                extra={"model": "NAS326", "busybox": "BusyBox v1.19.4 (2020-01-14 02:32:09 CST)",
                       "kernel_builder": "root@nas-build"},
            ),
        ),
        # Marvell Armada 385, single core
        hardware=(Hardware(cpus=1, cpu_model="ARMv7 Processor rev 1 (v7l)", mem_kb=262144, disk_kb=389120000,
                           board="Marvell Armada 380/385 (Device Tree)"),),
        hostnames=("NAS326",),
        builder=build_iot_nas,
        vulnerability="Zyxel NAS326 CVE-2020-9054-style NAS fingerprint",
    ),
    Persona(
        id="openwrt-1806", family="openwrt", kind="device", os_id="openwrt",
        user="root", uid=0, gid=0, shell="/bin/ash",
        arch="linux-mips-lsb", hardware_platform="mips", operating_system="Linux",
        ssh_profile="dropbear", package_managers=("opkg",),
        releases=(
            Release(
                label="18.06.2",
                ssh_banner="SSH-2.0-dropbear_2017.75",
                ssh_version="Dropbear sshd 2017.75",
                kernel_version="4.14.95",
                kernel_build="#0 Mon Jan 28 08:54:02 2019",
                os_version="18.06.2",
                os_version_id="18.06.2",
                extra={"revision": "r7676-cddd7b4c77", "target": "ramips/mt7620", "pkg_arch": "mipsel_24kc",
                       "busybox": "BusyBox v1.28.4 ()", "kernel_builder": "builder@buildhost"},
            ),
        ),
        hardware=(MIPS_ROUTER,),
        hostnames=("OpenWrt",),
        builder=build_openwrt,
        vulnerability="OpenWrt 18.06 Dropbear router fingerprint",
        remove_paths=("home/root", "etc/motd"),
    ),
    Persona(
        id="qnap-qts", family="qnap", kind="device", os_id="qnap",
        user="admin", uid=1000, gid=1000, shell="/bin/sh",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=("qpkg_cli",),
        releases=(
            Release(
                label="5.1.0",
                ssh_banner="SSH-2.0-OpenSSH_8.4",
                ssh_version="OpenSSH_8.4p1, OpenSSL 1.1.1t 7 Feb 2023",
                kernel_version="5.10.60-qnap",
                kernel_build="#1 SMP Tue Sep 12 01:23:45 CST 2023",
                os_version="5.1.0",
                os_version_id="5.1.0",
                extra={"model": "TS-451+", "build": "20230912", "kernel_builder": "root@U16BuildServer141"},
            ),
        ),
        # TS-451+: Celeron J1900 (4 cores), 2 GB
        hardware=(Hardware(cpus=4, cpu_model="Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz",
                           mem_kb=1945988, disk_kb=389120000),),
        hostnames=("NAS4BAY",),
        builder=build_qnap,
        vulnerability="QNAP QTS NAS fingerprint",
    ),
    Persona(
        id="synology-dsm", family="synology", kind="device", os_id="synology",
        user="admin", uid=1000, gid=1000, shell="/bin/sh",
        arch="linux-x64-lsb", hardware_platform="x86_64", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=("synopkg",),
        releases=(
            Release(
                label="7.1.1-42962",
                ssh_banner="SSH-2.0-OpenSSH_8.2",
                ssh_version="OpenSSH_8.2p1, OpenSSL 1.1.1t 7 Feb 2023",
                kernel_version="4.4.302+",
                kernel_build="#64570 SMP Fri Jul 21 00:00:00 CST 2023",
                os_version="7.1.1",
                os_version_id="7.1",
                extra={"build": "42962", "smallfix": "6", "builddate": "2023/06/05",
                       "kernel_builder": "root@build3"},
            ),
        ),
        # Atom C3538 (4 cores, matches the C3000 chipset of lspci), 4 GB
        hardware=(Hardware(cpus=4, cpu_model="Intel(R) Atom(TM) CPU C3538 @ 2.10GHz",
                           mem_kb=3933060, disk_kb=389120000),),
        hostnames=("DiskStation",),
        builder=build_synology,
        vulnerability="Synology DSM 7.1 NAS fingerprint",
    ),
    Persona(
        id="ubiquiti-edgerouter-x", family="ubiquiti", kind="device", os_id="edgeos",
        user="ubnt", uid=1000, gid=1000, shell="/bin/vbash",
        arch="linux-mips-lsb", hardware_platform="mips", operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=("apt-get", "dpkg"),
        releases=(
            Release(
                label="2.0.9-hotfix.7",
                ssh_banner="SSH-2.0-OpenSSH_7.4",
                ssh_version="OpenSSH_7.4p1, OpenSSL 1.0.2k-fips 26 Jan 2017",
                kernel_version="4.14.54-UBNT",
                kernel_build="#1 SMP Thu May 25 12:12:35 UTC 2023",
                os_version="v2.0.9-hotfix.7",
                os_version_id="2.0.9",
                packages={"dpkg": "1.19.8"},
                extra={"model": "EdgeRouter X", "kernel_builder": "builder@edgeos-build"},
            ),
        ),
        hardware=(MIPS_ROUTER,),
        hostnames=("ubnt",),
        builder=build_ubiquiti,
        vulnerability="Ubiquiti EdgeRouter CVE-2023-2377-style EdgeOS fingerprint",
    ),
)


def ids():
    return [p.id for p in PERSONAS]


def by_id(pid):
    for p in PERSONAS:
        if p.id == pid:
            return p
    raise PersonaError(f'unknown persona "{pid}", allowed: {" ".join(ids())}')
