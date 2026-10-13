"""The persona catalog as data. Each persona has fixed traits (family, user,
architecture, package managers, SSH profile) and pools (releases, hardware,
host names) from which every start draws one Identity.

Servers (2026 catalog): every release is one consistent patch level of a real
system (banner, ssh -V, kernel, OpenSSL, package versions of the same weeks),
with its source above it. Values marked "unverified" could not be checked
against a public source and were chosen to fit their verified neighbours.
Release.extra["first_install"] is the earliest installation (OS release or
image date), Release.extra["updated"] the day of the update that brought the
release's versions; the identity installs not before the first, boots after the
second and after its kernel was built, and logs the upgrades between them.

Devices (2026 catalog): every release is one firmware image of the vendor, with
its download or release-note page above it (Release.extra["firmware"]): the
device was set up with it on or after the day it came out and still runs it, so
its versions go stale only when the next firmware came out.
"""
import re
from dataclasses import replace

from . import PersonaError
from .devices import (build_dlink, build_ipcam, build_openwrt, build_qnap, build_synology, build_tplink, build_ubiquiti,
                      build_zyxel)
from .model import Hardware, Persona, Release
from .servers import build_centos, build_debian, build_gpu_ubuntu, build_raspios, build_rhel, build_ubuntu

APT = ("apt", "apt-get", "dpkg")
DNF = ("dnf", "yum", "rpm")
YUM = ("yum", "rpm")

# --- hardware ---------------------------------------------------------------------
# The provider ties host names, kernels, lspci and the root disk to the hardware.
# VMs: MemTotal is a few percent below the nominal size (kernel and firmware
# reservations, unverified per provider); /dev/shm in df is half of MemTotal.
# AWS Nitro: t3.medium (8259CL, 2 vCPU, 4 GiB), m6a.xlarge (7R13, 4 vCPU, 16 GiB), 8 / 20 GB gp3 root
AWS_2 = Hardware(cpus=2, cpu_model="Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz", mem_kb=3964928,
                 disk_kb=7941576, provider="aws")
AWS_4 = Hardware(cpus=4, cpu_model="AMD EPYC 7R13 Processor", mem_kb=16106252, disk_kb=20134592, provider="aws")
# KVM VPS of a hoster with QEMU's named CPU models
KVM_1 = Hardware(cpus=1, cpu_model="Intel Xeon Processor (Icelake)", mem_kb=985332, disk_kb=20134592,
                 provider="kvm")
KVM_2 = Hardware(cpus=2, cpu_model="AMD EPYC-Milan Processor", mem_kb=3964928, disk_kb=40470732, provider="kvm")
KVM_4 = Hardware(cpus=4, cpu_model="Intel Xeon Processor (Icelake)", mem_kb=8023548, disk_kb=81106868,
                 provider="kvm")
KVM_8 = Hardware(cpus=8, cpu_model="Intel Xeon Processor (Icelake)", mem_kb=16359660, disk_kb=162420480,
                 provider="kvm")
# DigitalOcean basic droplet s-2vcpu-4gb (80 GB); unverified: the "DO-Regular" model name of basic droplets
DO_2 = Hardware(cpus=2, cpu_model="DO-Regular", mem_kb=3964928, disk_kb=81106868, provider="do")

PI4 = "Raspberry Pi 4 Model B Rev 1.5"
PI5 = "Raspberry Pi 5 Model B Rev 1.0"
# 32 / 64 GB SD cards; MemTotal less the firmware's share (unverified)
PI_HARDWARE = (
    Hardware(cpus=4, cpu_model="Cortex-A72", mem_kb=3881084, disk_kb=30358348, board=PI4, provider="pi"),
    Hardware(cpus=4, cpu_model="Cortex-A72", mem_kb=7998564, disk_kb=60801848, board=PI4, provider="pi"),
    Hardware(cpus=4, cpu_model="Cortex-A76", mem_kb=4139168, disk_kb=30358348, board=PI5, provider="pi"),
    Hardware(cpus=4, cpu_model="Cortex-A76", mem_kb=8245920, disk_kb=60801848, board=PI5, provider="pi"),
)
PI_FLAVORS = {PI4: "v8", PI5: "2712"}  # linux-image-*-rpi-v8 / -rpi-2712

# GPU servers on bare metal: a DGX A100 (2x EPYC 7742, 1 TB, 8x A100; SMT off), a two-L40S
# inference box, a rented RTX 4090
GPU_HARDWARE = (
    Hardware(cpus=128, cpu_model="AMD EPYC 7742 64-Core Processor", mem_kb=1056622596, disk_kb=3750221592,
             gpus=("NVIDIA A100-SXM4-80GB",) * 8, gpu_mem_mib=81920, provider="dgx"),
    Hardware(cpus=64, cpu_model="AMD EPYC 9354 32-Core Processor", mem_kb=528280640, disk_kb=1875110796,
             gpus=("NVIDIA L40S",) * 2, gpu_mem_mib=46068, provider="gpu"),
    Hardware(cpus=32, cpu_model="AMD EPYC 7543 32-Core Processor", mem_kb=263932668, disk_kb=1875110796,
             gpus=("NVIDIA GeForce RTX 4090",), gpu_mem_mib=24564, provider="gpu"),
)


# --- release helpers ----------------------------------------------------------------

def _chain(*releases, stale=None):
    """A pool of patch levels in update order: each one is superseded on the day
    the next one came out. stale: per level, the day the next openssh or kernel
    security update came out (None: unknown); identity.stale_at takes the
    earliest of it, the next level and 90 days after the update, and the logs
    of a system never pull the level's versions after that."""
    stale = stale or (None,) * len(releases)
    assert len(stale) == len(releases)
    out = []
    for i, r in enumerate(releases):
        extra = {k: v for k, v in r.extra.items() if k not in ("superseded", "stale_from")}
        if i + 1 < len(releases):
            assert releases[i + 1].extra["updated"] > r.extra["updated"], r.label
            extra["superseded"] = releases[i + 1].extra["updated"]
        if stale[i]:
            assert stale[i] > r.extra["updated"], r.label
            extra["stale_from"] = stale[i]
        out.append(replace(r, extra=extra))
    return tuple(out)


def _medium(release):
    """The patch level of an installation image (point release)."""
    return replace(release, extra={**release.extra, "medium": True})


def _firmware(label, released, superseded, banner, ssh_version, kernel, uts, builder, compiler, os_version,
              os_version_id, packages=None, **extra):
    """A device firmware: installed (set up) from the day it came out
    (first_install = updated), stale only when the vendor's next firmware came
    out (superseded, None for the last one)."""
    days = {"first_install": released, "updated": released}
    if superseded:
        assert superseded > released, label
        days["superseded"] = superseded
    return Release(label=label, ssh_banner=banner, ssh_version=ssh_version, kernel_version=kernel,
                   kernel_build=uts, os_version=os_version, os_version_id=os_version_id, packages=packages or {},
                   extra={"firmware": True, **days, "kernel_builder": builder, "kernel_compiler": compiler,
                          **extra})


def _device(pid, family, os_id, user, shell, arch, ssh_profile, managers, releases, hardware, hostnames, builder,
            summary, uid=1000, remove_paths=(), max_install_days=900):
    # uname -o / -a: BusyBox prints "GNU/Linux" (coreutils/uname.c 1.14.1 and 1.19.2 hard-code it, 1.35.0
    #   takes CONFIG_UNAME_OSNAME, default "GNU/Linux"), as coreutils does on the Debian-based EdgeOS
    operating_system = "GNU/Linux"
    return Persona(
        id=pid, family=family, kind="device", os_id=os_id,
        user=user, uid=uid, gid=uid, shell=shell,
        arch=arch[0], hardware_platform=arch[1], operating_system=operating_system,
        ssh_profile=ssh_profile, package_managers=managers,
        releases=releases, hardware=hardware, hostnames=hostnames,
        builder=builder, summary=summary, remove_paths=remove_paths,
        max_install_days=max_install_days,
    )


def _ubuntu(updated, codename, os_version, openssh, openssh_before, kernel, uts, builder, compiler, openssl, packages,
            flavor="generic", providers=("kvm", "do")):
    """One Ubuntu patch level. openssh is the package version (1:9.6p1-3ubuntu13.14)."""
    upstream, revision = openssh.split(":", 1)[1].split("-", 1)
    names = {"noble": ("24.04", "Noble Numbat", "trixie/sid", "2024-04-25"),
             "jammy": ("22.04", "Jammy Jellyfish", "bookworm/sid", "2022-04-21")}
    version_id, long_name, debian_version, first = names[codename]
    return Release(
        label=f"{os_version.split()[0]} {kernel}",
        ssh_banner=f"SSH-2.0-OpenSSH_{upstream} Ubuntu-{revision}",
        ssh_version=f"OpenSSH_{upstream} Ubuntu-{revision}, {openssl}",
        kernel_version=f"{kernel}-{flavor}",
        kernel_build=uts,
        os_version=os_version,
        os_version_id=version_id,
        packages={"openssh-client": openssh, "openssh-server": openssh, **packages},
        extra={"codename": codename, "codename_long": long_name, "debian_version": debian_version,
               "kernel_builder": f"buildd@{builder}", "kernel_compiler": compiler, "first_install": first,
               "updated": updated, "providers": providers,
               "upgrades": {"openssh-client": openssh_before, "openssh-server": openssh_before}},
    )


def _debian(updated, codename, point, portable, openssh, openssh_before, kernel, kernel_pkg, kernel_date, compiler,
            openssl, packages):
    """One Debian patch level. portable is what sshd names (9.2p1, 10.0p2)."""
    revision = openssh.split("-", 1)[1]
    major = {"bookworm": "12", "trixie": "13"}[codename]
    extra = {"codename": codename, "debian_version": point, "version_full": codename == "trixie",
             "kernel_builder": "debian-kernel@lists.debian.org", "kernel_compiler": compiler,
             "first_install": {"bookworm": "2023-06-10", "trixie": "2025-08-09"}[codename], "updated": updated}
    if openssh_before:
        extra["upgrades"] = {"openssh-client": openssh_before, "openssh-server": openssh_before}
    return Release(
        label=f"{point} {kernel_pkg}",
        ssh_banner=f"SSH-2.0-OpenSSH_{portable} Debian-{revision}",
        ssh_version=f"OpenSSH_{portable} Debian-{revision}, {openssl}",
        kernel_version=kernel,
        kernel_build=f"#1 SMP PREEMPT_DYNAMIC Debian {kernel_pkg} ({kernel_date})",
        os_version=f"{major} ({codename})",
        os_version_id=major,
        packages={"openssh-client": openssh, "openssh-server": openssh, **packages},
        extra=extra,
    )


_BOOKWORM_GCC = "gcc-12 (Debian 12.2.0-14) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
# gcc-12 12.2.0-14+deb12u1 entered bookworm with 12.11 (changelog 2025-04-07); its use for
# the later kernels is unverified
_BOOKWORM_GCC_U1 = "gcc-12 (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
# unverified: the cross-prefixed compiler name of the trixie kernel builds
_TRIXIE_GCC = "x86_64-linux-gnu-gcc-14 (Debian 14.2.0-19) 14.2.0, GNU ld (GNU Binutils for Debian) 2.44"
_BOOKWORM_PKGS = {"apt": "2.6.1", "dpkg": "1.21.22", "htop": "3.2.2-2"}
_TRIXIE_PKGS = {"apt": "3.0.3", "dpkg": "1.22.21", "htop": "3.4.1-5",
                "vim": "2:9.1.1230-2"}


def _raspios(updated, image, pi_gen, point, openssh, openssh_before, kernel, kernel_pkg, kernel_date, compiler, openssl,
             packages):
    """Raspberry Pi OS Bookworm arm64: Debian's userland, the Pi kernel per board."""
    release = _debian(updated, "bookworm", point, "9.2p1", openssh, openssh_before, f"{kernel}+rpt-rpi-{{flavor}}",
                      kernel_pkg, kernel_date, compiler, openssl, packages)
    return replace(release, label=f"{image} {kernel_pkg}",
                   kernel_build=f"#1 SMP PREEMPT Debian {kernel_pkg} ({kernel_date})",
                   extra={**release.extra, "kernel_builder": "serge@raspberrypi.com", "flavors": PI_FLAVORS,
                          "image": image, "pi_gen": pi_gen, "first_install": image})


def _rhel(updated, minor, kernel, uts, builder, compiler, openssh, openssh_before, openssl, packages):
    return Release(
        label=f"9.{minor} {kernel}",
        ssh_banner="SSH-2.0-OpenSSH_8.7",
        ssh_version=f"OpenSSH_8.7p1, {openssl}",
        kernel_version=f"{kernel}.el9_{minor}.x86_64",
        kernel_build=uts,
        os_version=f"9.{minor} (Plow)",
        os_version_id=f"9.{minor}",
        packages={"dnf": "4.14.0", "rpm": "4.16.1.3", "openssh-server": openssh, **packages},
        # ssh_rsa_sha1: the DEFAULT crypto policy of RHEL 9 has no SHA-1 signatures, sshd offers its RSA
        #   key as rsa-sha2-512,rsa-sha2-256 only (unverified: from the opensshserver back-end of
        #   crypto-policies, HostKeyAlgorithms without ssh-rsa)
        extra={"kernel_builder": builder, "kernel_compiler": compiler, "first_install": "2022-05-17",
               "updated": updated, "ssh_rsa_sha1": False,
               "upgrades": {"openssh-server": openssh_before}},
    )


def _centos(updated, kernel, uts, openssh, openssh_before, packages):
    extra = {"centos_release": "7.9.2009", "kernel_builder": "mockbuild@kbuilder.bsys.centos.org",
             "kernel_compiler": "gcc version 4.8.5 20150623 (Red Hat 4.8.5-44) (GCC) ",
             "first_install": "2020-11-12", "updated": updated}
    if openssh_before:
        extra["upgrades"] = {"openssh-server": openssh_before}
    return Release(
        label=f"7.9.2009 {kernel}",
        ssh_banner="SSH-2.0-OpenSSH_7.4",
        # OpenSSL 1.0.2 puts two spaces before the date
        ssh_version="OpenSSH_7.4p1, OpenSSL 1.0.2k-fips  26 Jan 2017",
        kernel_version=f"3.10.0-{kernel}.el7.x86_64",
        kernel_build=uts,
        os_version="7 (Core)",
        os_version_id="7",
        packages={"yum": "3.4.3-168.el7.centos", "openssh-server": openssh, "wget": "1.14-18.el7_6.1",
                  "net-tools": "2.0-0.25.20131004git.el7", **packages},
        extra=extra,
    )


# --- Ubuntu 24.04 ---------------------------------------------------------------------
# Versions: Launchpad API getPublishedSources (openssh, linux, curl, git, vim, dpkg, apt, base-files)
#   https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedSources&source_name=openssh
#   base-files 13ubuntu10.3 (24.04.3) 2025-08-04, 13ubuntu10.4 (24.04.4) 2026-02-09
# uname -v: verbatim from the "Kernel version:" line of Launchpad build logs, whose builders run these
#   kernels; the builder host and gcc-13 version from the build record and log of each kernel, e.g.
#   https://launchpad.net/~canonical-kernel-team/+archive/ubuntu/ppa/+build/32385321
# OpenSSL 3.0.13 30 Jan 2024: https://github.com/openssl/openssl/blob/openssl-3.0.13/VERSION.dat
_NOBLE_GCC = "x86_64-linux-gnu-gcc-13 (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0, GNU ld (GNU Binutils for Ubuntu) 2.42"
_NOBLE_GCC_1 = ("x86_64-linux-gnu-gcc-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, "
                "GNU ld (GNU Binutils for Ubuntu) 2.42")
_NOBLE_SSL = "OpenSSL 3.0.13 30 Jan 2024"
_NOBLE_LEVELS = (
    # forgotten since this update (not in the pool: the -aws pool has this time)
    _ubuntu("2025-10-20", "noble", "24.04.3 LTS", "1:9.6p1-3ubuntu13.14", "1:9.6p1-3ubuntu13.13", "6.8.0-87",
            "#88-Ubuntu SMP PREEMPT_DYNAMIC Sat Oct 11 09:28:41 UTC 2025", "lcy02-amd64-034", _NOBLE_GCC,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.6", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    _ubuntu("2026-02-25", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.14", "1:9.6p1-3ubuntu13.13", "6.8.0-101",
            "#101-Ubuntu SMP PREEMPT_DYNAMIC Mon Feb  9 10:15:05 UTC 2026", "lcy02-amd64-051", _NOBLE_GCC,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.7", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    _ubuntu("2026-05-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.16", "1:9.6p1-3ubuntu13.15", "6.8.0-117",
            "#117-Ubuntu SMP PREEMPT_DYNAMIC Tue May  5 19:26:24 UTC 2026", "lcy02-amd64-102", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.9", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.13", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    # 13.17 was only in -proposed
    _ubuntu("2026-07-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.18", "1:9.6p1-3ubuntu13.16", "6.8.0-136",
            "#136-Ubuntu SMP PREEMPT_DYNAMIC Wed Jul  1 21:53:05 UTC 2026", "lcy02-amd64-041", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.11", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.18", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
)

# Ubuntu on AWS runs the rolling linux-aws kernel of the linux-image-aws metapackage (6.14 in 2025, 6.17
#   from 2026-02), Launchpad getPublishedBinaries linux-image-aws; uname -v of the aws flavor has no
#   PREEMPT_DYNAMIC, verbatim example "6.14.0-1009-aws #9~24.04.1-Ubuntu SMP Fri Jun 27 17:50:08 UTC 2025"
#   from https://github.com/k3s-io/k3s/issues/12711; builder and gcc from the Launchpad build records
# unverified: the time of day of uname -v (build start + 87 min, the offset of the verified example)
_NOBLE_AWS_LEVELS = (
    _ubuntu("2025-10-25", "noble", "24.04.3 LTS", "1:9.6p1-3ubuntu13.14", "1:9.6p1-3ubuntu13.13", "6.14.0-1015",
            "#15~24.04.1-Ubuntu SMP Tue Sep 23 22:40:31 UTC 2025", "lcy02-amd64-042", _NOBLE_GCC,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.6", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}, flavor="aws", providers=("aws",)),
    _ubuntu("2026-07-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.18", "1:9.6p1-3ubuntu13.16", "6.17.0-1019",
            "#19~24.04.1-Ubuntu SMP Tue Jun 23 19:03:52 UTC 2026", "lcy02-amd64-070", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.11", "git": "1:2.43.0-1ubuntu7.3",
                         "vim": "2:9.1.0016-1ubuntu7.18", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}, flavor="aws", providers=("aws",)),
)
# stale: next linux / openssh in noble-security|updates (Launchpad): 6.8.0-106 / openssh 13.15 2026-03-12,
#   6.8.0-124 2026-06-02, 6.8.0-137 2026-08-05; linux-image-aws 6.14.0-1016 2025-11-03, 7.0.0-1009 2026-07-27
NOBLE = (_chain(*_NOBLE_LEVELS[1:], stale=("2026-03-12", "2026-06-02", "2026-08-05"))
         + _chain(*_NOBLE_AWS_LEVELS, stale=("2025-11-03", "2026-07-27")))

# --- Ubuntu 22.04 ---------------------------------------------------------------------
# Versions: Launchpad API getPublishedSources, as for noble (openssh 3ubuntu0.12 never left -proposed)
# Builder host and gcc-11 / binutils of each kernel: its Launchpad build record and log, e.g.
#   https://launchpad.net/~canonical-kernel-team/+archive/ubuntu/ppa2/+build/33476242
# unverified: the time of day of uname -v (build start + 75 min, the offset of the verified noble kernels)
# OpenSSL 3.0.2 15 Mar 2022: https://github.com/openssl/openssl/blob/openssl-3.0.2/VERSION.dat
_JAMMY_SSL = "OpenSSL 3.0.2 15 Mar 2022"


def _jammy_gcc(revision, binutils="2.38"):
    return f"gcc (Ubuntu 11.4.0-1ubuntu1~22.04{revision}) 11.4.0, GNU ld (GNU Binutils for Ubuntu) {binutils}"


_JAMMY_NET_TOOLS = "1.60+git20181103.0eebece-1ubuntu5"
_JAMMY_LEVELS = (
    # forgotten since this update
    _ubuntu("2025-05-20", "jammy", "22.04.5 LTS", "1:8.9p1-3ubuntu0.13", "1:8.9p1-3ubuntu0.11", "5.15.0-140",
            "#150-Ubuntu SMP Sat Apr 12 07:11:09 UTC 2025", "lcy02-amd64-024", _jammy_gcc(""), _JAMMY_SSL,
            {"apt": "2.4.13", "dpkg": "1.21.1ubuntu2.3", "curl": "7.81.0-1ubuntu1.20", "git": "1:2.34.1-1ubuntu1.12",
             "vim": "2:8.2.3995-1ubuntu2.24", "htop": "3.0.5-7build2", "net-tools": _JAMMY_NET_TOOLS}),
    _ubuntu("2025-12-15", "jammy", "22.04.5 LTS", "1:8.9p1-3ubuntu0.13", "1:8.9p1-3ubuntu0.11", "5.15.0-164",
            "#174-Ubuntu SMP Fri Nov 14 21:26:47 UTC 2025", "lcy02-amd64-114", _jammy_gcc(".2"), _JAMMY_SSL,
            {"apt": "2.4.14", "dpkg": "1.21.1ubuntu2.6", "curl": "7.81.0-1ubuntu1.21", "git": "1:2.34.1-1ubuntu1.15",
             "vim": "2:8.2.3995-1ubuntu2.24", "htop": "3.0.5-7build2", "net-tools": _JAMMY_NET_TOOLS + ".4"}),
    _ubuntu("2026-04-20", "jammy", "22.04.5 LTS", "1:8.9p1-3ubuntu0.14", "1:8.9p1-3ubuntu0.13", "5.15.0-176",
            "#186-Ubuntu SMP Fri Mar 13 12:10:31 UTC 2026", "lcy02-amd64-024", _jammy_gcc(".3"), _JAMMY_SSL,
            {"apt": "2.4.14", "dpkg": "1.21.1ubuntu2.6", "curl": "7.81.0-1ubuntu1.23", "git": "1:2.34.1-1ubuntu1.17",
             "vim": "2:8.2.3995-1ubuntu2.27", "htop": "3.0.5-7build2", "net-tools": _JAMMY_NET_TOOLS + ".4"}),
    _ubuntu("2026-08-25", "jammy", "22.04.5 LTS", "1:8.9p1-3ubuntu0.16", "1:8.9p1-3ubuntu0.15", "5.15.0-190",
            "#200-Ubuntu SMP Fri Aug  7 16:14:52 UTC 2026", "lcy02-amd64-101", _jammy_gcc(".3"), _JAMMY_SSL,
            {"apt": "2.4.14", "dpkg": "1.21.1ubuntu2.6", "curl": "7.81.0-1ubuntu1.27", "git": "1:2.34.1-1ubuntu1.17",
             "vim": "2:8.2.3995-1ubuntu2.36", "htop": "3.0.5-7build2", "net-tools": _JAMMY_NET_TOOLS + ".4"}),
)
# stale: next linux / openssh of jammy (Launchpad): 5.15.0-141 2025-06-03, 5.15.0-168 2026-02-04,
#   openssh 0ubuntu0.15 2026-04-29, 5.15.0-191 2026-09-23
JAMMY = _chain(*_JAMMY_LEVELS, stale=("2025-06-03", "2026-02-04", "2026-04-29", "2026-09-23"))

# --- Debian 13 ------------------------------------------------------------------------
# openssh: https://metadata.ftp-master.debian.org/changelogs/main/o/openssh/stable_changelog
#   sshd says 10.0p2: SSH_PORTABLE "p2" in https://sources.debian.org/src/openssh/1:10.0p1-7+deb13u4/version.h
# kernel: https://metadata.ftp-master.debian.org/changelogs/main/l/linux/stable_changelog (uname -v takes
#   the changelog date), names 6.12.x+deb13-amd64 from https://deb.debian.org/debian/pool/main/l/linux-signed-amd64/
# point releases: base-files changelog (DEBIAN_VERSION_FULL in os-release since 13.8)
#   https://sources.debian.org/src/base-files/13.8+deb13u7/etc/os-release
# OpenSSL dates: VERSION.dat of the openssl-3.5.x tags on github.com/openssl/openssl
_TRIXIE_LEVELS = (
    # 13.1, forgotten since this update
    _debian("2025-09-08", "trixie", "13.1", "10.0p2", "1:10.0p1-7", None,
            "6.12.43+deb13-amd64", "6.12.43-1", "2025-08-27",
            _TRIXIE_GCC, "OpenSSL 3.5.1 1 Jul 2025",
            {**_TRIXIE_PKGS, "curl": "8.14.1-2", "sudo": "1.9.16p2-3"}),
    # 13.2
    _debian("2025-11-20", "trixie", "13.2", "10.0p2", "1:10.0p1-7", None,
            "6.12.57+deb13-amd64", "6.12.57-1", "2025-11-05",
            _TRIXIE_GCC, "OpenSSL 3.5.4 30 Sep 2025",
            {**_TRIXIE_PKGS, "curl": "8.14.1-2+deb13u2", "sudo": "1.9.16p2-3"}),
    # 13.3 (before 13.4 brought openssh deb13u1 and OpenSSL 3.5.5)
    _debian("2026-03-05", "trixie", "13.3", "10.0p2", "1:10.0p1-7", None,
            "6.12.74+deb13-amd64", "6.12.74-1", "2026-03-01",
            _TRIXIE_GCC, "OpenSSL 3.5.4 30 Sep 2025",
            {**_TRIXIE_PKGS, "curl": "8.14.1-2+deb13u2", "sudo": "1.9.16p2-3+deb13u1"}),
    # 13.5
    _debian("2026-05-20", "trixie", "13.5", "10.0p2", "1:10.0p1-7+deb13u4", "1:10.0p1-7+deb13u2",
            "6.12.88+deb13-amd64", "6.12.88-1", "2026-05-15",
            _TRIXIE_GCC, "OpenSSL 3.5.6 7 Apr 2026",
            {**_TRIXIE_PKGS, "dpkg": "1.22.22", "curl": "8.14.1-2+deb13u4", "sudo": "1.9.16p2-3+deb13u2"}),
    # 13.7
    _debian("2026-09-01", "trixie", "13.7", "10.0p2", "1:10.0p1-7+deb13u4", "1:10.0p1-7+deb13u2",
            "6.12.107+deb13-amd64", "6.12.107-1", "2026-08-29",
            _TRIXIE_GCC, "OpenSSL 3.5.7 9 Jun 2026",
            {**_TRIXIE_PKGS, "dpkg": "1.22.22", "curl": "8.14.1-2+deb13u5", "sudo": "1.9.16p2-3+deb13u2"}),
)
# 13.1 and 13.2 are the versions of their installation images
# stale: the next kernel of trixie(-security), a day after its changelog date (security) or the point release:
#   6.12.48-1 2025-09-21, 6.12.63-1 with 13.3 2026-01-10, 6.12.74-2 2026-03-09, 6.12.90-1 2026-05-23;
#   unknown after 6.12.107-1 (90 days)
TRIXIE = _chain(_medium(_TRIXIE_LEVELS[0]), _medium(_TRIXIE_LEVELS[1]), *_TRIXIE_LEVELS[2:],
                stale=("2025-09-21", "2026-01-10", "2026-03-09", "2026-05-23", None))

# --- Debian 12 ------------------------------------------------------------------------
# openssh: https://metadata.ftp-master.debian.org/changelogs/main/o/openssh/oldstable_changelog
#   1:9.2p1-2+deb12u3 of 2024-07-01, https://tracker.debian.org/pkg/openssh
# kernel: https://metadata.ftp-master.debian.org/changelogs/main/l/linux/oldstable_changelog ("Bump ABI to N");
#   6.1.0-47 / -50 from https://deb.debian.org/debian/pool/main/l/linux-signed-amd64/
# point releases: https://metadata.ftp-master.debian.org/changelogs/main/b/base-files/oldstable_changelog
# OpenSSL dates: VERSION.dat of the openssl-3.0.x tags on github.com/openssl/openssl
_BOOKWORM_LEVELS = (
    # 12.2, installation image
    _debian("2023-10-10", "bookworm", "12.2", "9.2p1", "1:9.2p1-2+deb12u1", "1:9.2p1-2",
            "6.1.0-13-amd64", "6.1.55-1", "2023-09-29",
            _BOOKWORM_GCC, "OpenSSL 3.0.11 19 Sep 2023",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u4", "vim": "2:9.0.1378-2",
             "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.5, installation image
    _debian("2024-02-12", "bookworm", "12.5", "9.2p1", "1:9.2p1-2+deb12u2", "1:9.2p1-2+deb12u1",
            "6.1.0-18-amd64", "6.1.76-1", "2024-02-01",
            _BOOKWORM_GCC, "OpenSSL 3.0.11 19 Sep 2023",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u5", "vim": "2:9.0.1378-2",
             "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.11
    _debian("2025-05-20", "bookworm", "12.11", "9.2p1", "1:9.2p1-2+deb12u6", "1:9.2p1-2+deb12u5",
            "6.1.0-36-amd64", "6.1.139-1", "2025-05-18",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.16 11 Feb 2025",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u12",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.14
    _debian("2026-05-12", "bookworm", "12.14", "9.2p1", "1:9.2p1-2+deb12u10", "1:9.2p1-2+deb12u9",
            "6.1.0-47-amd64", "6.1.170-3", "2026-05-08",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.20 7 Apr 2026",
            {**_BOOKWORM_PKGS, "dpkg": "1.21.23", "curl": "7.88.1-10+deb12u15",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u4"}),
    # 12.15
    _debian("2026-07-06", "bookworm", "12.15", "9.2p1", "1:9.2p1-2+deb12u10", "1:9.2p1-2+deb12u9",
            "6.1.0-50-amd64", "6.1.176-1", "2026-07-02",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.20 7 Apr 2026",
            {**_BOOKWORM_PKGS, "dpkg": "1.21.23", "curl": "7.88.1-10+deb12u15",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u4"}),
)
# 12.2 and 12.5 are the versions of their installation images: the older OpenSSH patch levels stay installable
# stale: the next kernel of bookworm(-security), as for trixie: 6.1.66-1 with 12.4 2023-12-10,
#   6.1.85-1 2024-04-12 (before openssh 1:9.2p1-2+deb12u3 of 2024-07-01), 6.1.140-1 2025-05-23,
#   6.1.172-1 2026-05-16; unknown after 6.1.176-1 (90 days)
BOOKWORM = _chain(_medium(_BOOKWORM_LEVELS[0]), _medium(_BOOKWORM_LEVELS[1]), *_BOOKWORM_LEVELS[2:],
                  stale=("2023-12-10", "2024-04-12", "2025-05-23", "2026-05-16", None))

# --- RHEL 9 ---------------------------------------------------------------------------
# Package NVRs: the RHEL rebuild Rocky Linux, https://dl.rockylinux.org/vault/rocky/9.4/BaseOS/x86_64/os/Packages/
#   (same versions; Rocky's own ".rocky.0.1" and ".0.1" rebuild suffixes left out)
# OpenSSL 3.0.7 1 Nov 2022 (9.4) / 3.2.2 4 Jun 2024 (9.5, 9.6): VERSION.dat of the openssl tags
_RHEL_COMPILER_94 = "gcc (GCC) 11.4.1 20231218 (Red Hat 11.4.1-4), GNU ld version 2.35.2-43.el9"
_RHEL9_LEVELS = (
    # 9.4 EUS; /proc/version verbatim from https://github.com/falcosecurity/falco/issues/3610
    _rhel("2025-02-20", 4, "5.14.0-427.55.1", "#1 SMP PREEMPT_DYNAMIC Mon Feb 10 12:37:38 EST 2025",
          "mockbuild@x86-64-03.build.eng.rdu2.redhat.com", _RHEL_COMPILER_94, "8.7p1-38.el9_4.4",
          "8.7p1-38.el9_4.1", "OpenSSL 3.0.7 1 Nov 2022",
          {"curl": "7.76.1-29.el9_4.1", "vim-enhanced": "2:8.2.2637-20.el9_1"}),
    # 9.5 GA kernel; uname -v verbatim from https://www.spec.org/cpu2017/results/res2025q2/cpu2017-20250529-48392.html
    # unverified: builder host and compiler (gcc 11.5.0 of RHEL 9.5)
    _rhel("2024-11-13", 5, "5.14.0-503.11.1", "#1 SMP PREEMPT_DYNAMIC Mon Sep 30 11:54:45 EDT 2024",
          "mockbuild@x86-64-01.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-2), GNU ld version 2.35.2-54.el9", "8.7p1-43.el9",
          "8.7p1-38.el9_4.4", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9", "vim-enhanced": "2:8.2.2637-21.el9"}),
    # 9.6, kernel of 2025-06; unverified: the time of uname -v, builder host and compiler
    _rhel("2025-06-05", 6, "5.14.0-570.18.1", "#1 SMP PREEMPT_DYNAMIC Thu May 15 08:45:21 EDT 2025",
          "mockbuild@x86-64-02.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-5), GNU ld version 2.35.2-63.el9", "8.7p1-45.el9",
          "8.7p1-43.el9", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9", "vim-enhanced": "2:8.2.2637-22.el9_6"}),
    # 9.6 EUS, kernel of 2026-08; uname -v verbatim from https://github.com/uxlfoundation/oneDNN/issues/6151
    # unverified: openssh / curl errata of 9.6 EUS after 2025, builder host and compiler
    _rhel("2026-08-25", 6, "5.14.0-570.135.1", "#1 SMP PREEMPT_DYNAMIC Wed Aug 12 22:16:32 EDT 2026",
          "mockbuild@x86-64-02.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-5), GNU ld version 2.35.2-63.el9", "8.7p1-45.el9",
          "8.7p1-43.el9", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9_6.1", "vim-enhanced": "2:8.2.2637-22.el9_6.1"}),
)
# in update order; 9.5 GA is the 9.5 installation image
# stale: the next kernel (Rocky vault dates): 503.14.1 2024-11-15, 570.19.1 2025-06-09; unknown for the
#   EUS kernels (90 days)
RHEL9 = _chain(_medium(_RHEL9_LEVELS[1]), _RHEL9_LEVELS[0], *_RHEL9_LEVELS[2:],
               stale=("2024-11-15", None, "2025-06-09", None))

# --- CentOS 7 -------------------------------------------------------------------------
# Packages and their dates: https://vault.centos.org/7.9.2009/updates/x86_64/Packages/ and .../os/x86_64/Packages/
# 3.10.0-1160.el7 uname -v verbatim from a boot log ("Mon Oct 19 16:18:59 UTC 2020", search result of
#   openbenchmarking.org); unverified: the uname -v times of the updates (vault file time less an hour)
_CENTOS7_LEVELS = (
    # 7.9.2009 as installed, never updated
    _centos("2020-11-12", "1160", "#1 SMP Mon Oct 19 16:18:59 UTC 2020", "7.4p1-21.el7", None,
            {"rpm": "4.11.3-45.el7", "curl": "7.29.0-59.el7",
             "vim-enhanced": "2:7.4.629-7.el7"}),
    _centos("2023-03-15", "1160.88.1", "#1 SMP Tue Mar  7 15:41:52 UTC 2023", "7.4p1-22.el7_9", "7.4p1-21.el7",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.1",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
    _centos("2023-12-13", "1160.105.1", "#1 SMP Thu Dec  7 15:39:45 UTC 2023", "7.4p1-23.el7_9", "7.4p1-22.el7_9",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.1",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
    # the last kernel before the end of life on 2024-06-30
    _centos("2024-06-10", "1160.119.1", "#1 SMP Tue Jun  4 14:43:51 UTC 2024", "7.4p1-23.el7_9", "7.4p1-22.el7_9",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.2",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
)
# 7.9.2009 as installed is the image; a later install would get the newest updates from the vault
# stale: the next kernel on the vault: 1160.90.1 2023-05-04, 1160.108.1 2024-01-26; none after 1160.119.1
#   (and the update of the 2020 image unknown): 90 days
CENTOS7 = _chain(_medium(_CENTOS7_LEVELS[0]), *_CENTOS7_LEVELS[1:], stale=(None, "2023-05-04", "2024-01-26", None))

# --- Raspberry Pi OS (Bookworm, arm64) ------------------------------------------------
# kernels and their changelog dates: debian.tar.xz of https://archive.raspberrypi.com/debian/pool/main/l/linux/
#   uname -v is "Debian <version> (<changelog date>)" (debian/rules.real), built by the Maintainer serge@raspberrypi.com
# image dates: https://downloads.raspberrypi.com/raspios_arm64/release_notes.txt, pi-gen commit = the tag
#   <date>-raspios-bookworm-arm64 of https://github.com/RPi-Distro/pi-gen; rpi-issue format: pi-gen scripts/common
# Debian userland: the bookworm changelogs (as for debian-bookworm) at the date of each update
# unverified: the compiler of the Pi kernel builds (the Debian gcc-12 of bookworm assumed)
_PI_GCC = "aarch64-linux-gnu-gcc-12 (Debian 12.2.0-14) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
_PI_GCC_U1 = "aarch64-linux-gnu-gcc-12 (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
# Every level is drawable at the time of writing (2026-10); a non-image level is only drawn while an
# installation 90-900 days back can still predate its stale day (2024-11-19 / 6.6.74: until about 2027-08).
_RASPIOS_LEVELS = (
    _raspios("2024-10-25", "2024-10-22", "17c8e4853490218fafcd0cc2f95203ccfebe043e", "12.7", "1:9.2p1-2+deb12u3",
             "1:9.2p1-2+deb12u2", "6.6.51", "1:6.6.51-1+rpt3", "2024-10-08", _PI_GCC, "OpenSSL 3.0.14 4 Jun 2024",
             {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u8", "git": "1:2.39.5-0+deb12u1", "vim": "2:9.0.1378-2",
              "sudo": "1.9.13p3-1+deb12u1"}),
    _raspios("2025-01-30", "2024-11-19", "891df1e21ed2b6099a2e6a13e26c91dea44b34d4", "12.9", "1:9.2p1-2+deb12u4",
             "1:9.2p1-2+deb12u3", "6.6.74", "1:6.6.74-1+rpt1", "2025-01-27", _PI_GCC, "OpenSSL 3.0.15 3 Sep 2024",
             {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u10", "git": "1:2.39.5-0+deb12u2",
              "vim": "2:9.0.1378-2+deb12u1", "sudo": "1.9.13p3-1+deb12u1"}),
    _raspios("2025-09-20", "2025-05-13", "5dabc7dc940059dfbc46af5d97b60a1e812523dd", "12.12", "1:9.2p1-2+deb12u7",
             "1:9.2p1-2+deb12u6", "6.12.47", "1:6.12.47-1+rpt1~bookworm", "2025-09-16", _PI_GCC_U1,
             "OpenSSL 3.0.17 1 Jul 2025",
             {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u14", "git": "1:2.39.5-0+deb12u2",
              "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u2"}),
    _raspios("2026-03-15", "2025-05-13", "5dabc7dc940059dfbc46af5d97b60a1e812523dd", "12.13", "1:9.2p1-2+deb12u7",
             "1:9.2p1-2+deb12u6", "6.12.75", "1:6.12.75-1+rpt1~bookworm", "2026-03-11", _PI_GCC_U1,
             "OpenSSL 3.0.18 30 Sep 2025",
             {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u14", "git": "1:2.39.5-0+deb12u3",
              "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u3"}),
    _raspios("2026-05-15", "2025-11-24", "67262a4ad0959aab2a9d84a6392b1967999e8f50", "12.14", "1:9.2p1-2+deb12u10",
             "1:9.2p1-2+deb12u9", "6.12.87", "1:6.12.87-1+rpt1~bookworm", "2026-05-12", _PI_GCC_U1,
             "OpenSSL 3.0.20 7 Apr 2026",
             {**_BOOKWORM_PKGS, "dpkg": "1.21.23", "curl": "7.88.1-10+deb12u15", "git": "1:2.39.5-0+deb12u3",
              "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u4"}),
)
# the 2024-10-22 image as written to the card
# stale: Pi kernel 6.6.62 2024-11-28 (archive date), openssh deb12u5 (security) 2025-02-15,
#   openssh deb12u9 (security) 2026-04-06; Pi kernels after 6.12.47 / 6.12.87 for bookworm: 90 days
RASPIOS = _chain(_medium(_RASPIOS_LEVELS[0]), *_RASPIOS_LEVELS[1:],
                 stale=("2024-11-28", "2025-02-15", None, "2026-04-06", None))

# --- GPU server (Ubuntu + NVIDIA) -----------------------------------------------------
# Drivers: Launchpad nvidia-graphics-drivers-550 / -570 (jammy, noble); CUDA 12.4 / 12.8 is the CUDA
#   version of the 550 / 570 branches (https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-570-211-01/)
# /proc/driver/nvidia/version format: "NVRM version: NVIDIA UNIX x86_64 Kernel Module  <ver>  <date>"
# unverified: the build dates in it (the file dates of https://download.nvidia.com/XFree86/Linux-x86_64/<ver>/)


def _gpu(base, driver, cuda, built, gcc):
    return replace(base, label=f"{base.label} nvidia-{driver}",
                   extra={**base.extra, "nvidia_driver": driver, "cuda": cuda, "nvidia_built": built,
                          "nvidia_gcc": gcc, "providers": ("dgx", "gpu")})


# the levels of the full Ubuntu chains (their superseded / stale days), with a driver on top
GPU_RELEASES = (
    _gpu(JAMMY[1], "550.163.01", "12.4", "Wed Apr  9 19:58:21 UTC 2025",
         "gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.2)"),
    _gpu(JAMMY[2], "570.211.01", "12.8", "Tue Dec  9 20:31:47 UTC 2025",
         "gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3)"),
    _gpu(NOBLE[0], "570.195.03", "12.8", "Tue Sep 23 16:12:09 UTC 2025",
         "gcc version 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04)"),
    _gpu(NOBLE[2], "570.211.01", "12.8", "Tue Dec  9 20:31:47 UTC 2025",
         "gcc version 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1)"),
)


# --- devices ----------------------------------------------------------------------------
# Each release is one firmware image of the vendor (its download or release-note
# page above it); kernel, BusyBox and SSH server as in that image unless marked
# unverified. A device runs the image it was set up with until the next one came out.
# Cowrie's [shell] arch picks the ELF header `cat` / `dd` show for a program: MT7628 / MT7621 run
#   little endian (OpenWrt mipsel_24kc), the QCA956x of the DIR-859 big endian (ath79, mips_24kc)
MIPS = ("linux-mips-lsb", "mips")
MIPS_BE = ("linux-mips-msb", "mips")
# the paths of Cowrie's Debian tree that a router, camera or NAS firmware does not have
VENDOR_ONLY = ("etc/os-release", "etc/debian_version", "etc/issue", "etc/issue.net", "etc/motd")

# DIR-859 A1/A3: QCA9563 (MIPS 74Kc), 64 MB (mem=64m of the built-in kernel command line), squashfs
#   of the 1.05B03 image; unverified: MemTotal
DLINK_HARDWARE = (Hardware(cpus=1, cpu_model="MIPS 74Kc V5.0", mem_kb=61496, disk_kb=5888, board="QCA956x"),)
_DLINK_KERNEL = ("2.6.31-svn58067", "#1 Fri Jun 24 11:05:39 CST 2016", "vic@localhost.localdomain",
                 "gcc version 4.3.3 (GCC) ")
DLINK = (
    # source: https://legacyfiles.us.dlink.com/DIR-859/REVA/FIRMWARE/ (DIR-859_REVA_FIRMWARE_1.05B03.zip:
    #   release note "June, 2016", kernel and BusyBox from the image; /etc/config of its squashfs)
    # unverified: the day of the release (the note names the month); the dropbear of the persona (the
    #   image has telnetd only), taken from the TP-Link firmware of the same SDK era
    _firmware("1.05B03", "2016-06-30", "2019-12-26", "SSH-2.0-dropbear_2012.55", "Dropbear sshd 2012.55",
              *_DLINK_KERNEL, "1.05", "1.05", busybox="BusyBox v1.14.1 (2016-06-28 10:53:08 CST)", buildrev="b03",
              image_sign="wrgac37_dlink.2013gui_dir859"),
    # source: https://legacyfiles.us.dlink.com/DIR-859/REVA/FIRMWARE/ (DIR-859_REVA_FIRMWARE_1.07B03_HOTFIX.zip,
    #   release note "December 26, 2019", the last firmware of the DIR-859)
    # unverified: kernel and BusyBox (the 1.07 images are encrypted): the kernel of 1.05B03, BusyBox
    #   rebuilt on the image date
    _firmware("1.07B03", "2019-12-26", None, "SSH-2.0-dropbear_2012.55", "Dropbear sshd 2012.55",
              *_DLINK_KERNEL, "1.07", "1.07", busybox="BusyBox v1.14.1 (2019-12-16 17:42:08 CST)", buildrev="b03",
              image_sign="wrgac37_dlink.2013gui_dir859"),
)

# TL-WR841N v14: MT7628NN (MIPS 24KEc, named 24Kc by 2.6 kernels), 4 MB flash, 32 MB; system type,
#   MemTotal and BogoMIPS unverified
TPLINK_HARDWARE = (Hardware(cpus=1, cpu_model="MIPS 24Kc V5.5", mem_kb=29436, disk_kb=2752, board="MT7628"),)
_TPLINK_GCC = "gcc version 4.6.3 (Buildroot 2012.11.1) "


def _tplink(build, rel, released, superseded, uts, builder, busybox):
    # the firmware version as the web UI shows it (unverified: "v0348.0")
    return _firmware(build, released, superseded, "SSH-2.0-dropbear_2012.55", "Dropbear sshd 2012.55", "2.6.36",
                     uts, builder, _TPLINK_GCC, f"0.9.1 4.19 v0348.0 Build {build} Rel.{rel}n", "0.9.1",
                     busybox=busybox)


# kernel, BusyBox and dropbear_2012.55 from the squashfs of each image (US firmware); released / superseded:
#   the dates of the download page (the next US image)
TPLINK = (
    # source: https://www.tp-link.com/us/support/download/tl-wr841n/v14/ (TL-WR841N(US)_V14_220816, 2022-11-23)
    _tplink("220816", "43928", "2022-11-23", "2023-11-24", "#1 Tue Aug 16 12:04:29 CST 2022", "jenkins@sohoici",
            "BusyBox v1.19.2 (2022-08-16 12:08:08 CST)"),
    # source: https://www.tp-link.com/us/support/download/tl-wr841n/v14/ (TL-WR841N(US)_V14_231119, 2023-11-24)
    _tplink("231119", "69901", "2023-11-24", "2025-01-21", "#1 Sun Nov 19 19:19:44 PST 2023", "zhang@ubuntu",
            "BusyBox v1.19.2 (2023-11-19 19:21:54 PST)"),
    # source: https://www.tp-link.com/us/support/download/tl-wr841n/v14/ (TL-WR841N(US)_V14_250328, 2025-04-18)
    _tplink("250328", "51324", "2025-04-18", "2026-01-14", "#1 Fri Mar 28 14:12:15 CST 2025",
            "jenkins@sohoicilinux4", "BusyBox v1.19.2 (2025-03-28 14:13:40 CST)"),
)

# NAS326: Marvell Armada 380 (one Cortex-A9 at 1.3 GHz), 512 MB, one or two disks in the volume;
#   unverified: MemTotal, BogoMIPS
ZYXEL_HARDWARE = (
    Hardware(cpus=1, cpu_model="ARMv7 Processor rev 1 (v7l)", mem_kb=506472, disk_kb=1921710080,
             board="Marvell Armada 380/385 (Device Tree)"),
    Hardware(cpus=1, cpu_model="ARMv7 Processor rev 1 (v7l)", mem_kb=506472, disk_kb=3843514368,
             board="Marvell Armada 380/385 (Device Tree)"),
)
_ZYXEL_GCC = "gcc version 4.8.5 20150209 (prerelease) (crosstool-NG 1.21.0) "
# OpenSSH 6.7p1 and OpenSSL 1.0.2h from the system image (sysdisk) of V5.21(AAZF.14)C0
_ZYXEL_SSH = ("SSH-2.0-OpenSSH_6.7p1", "OpenSSH_6.7p1, OpenSSL 1.0.2h  3 May 2016")
ZYXEL = (
    # source: https://www.zyxel.com/global/en/support/download?model=nas326 (521AAZF14C0.pdf, "June 1, 2023";
    #   kernel from the image)
    # unverified: superseded by AAZF.16 (2024-01-10, no AAZF.15 release note); BusyBox (not in the image strings)
    _firmware("5.21(AAZF.14)C0", "2023-06-01", "2024-01-10", *_ZYXEL_SSH, "3.10.39",
              "#1 Fri May 26 10:10:07 CST 2023", "root@twsvnnt01652-01", _ZYXEL_GCC, "V5.21(AAZF.14)C0", "5.21",
              busybox="BusyBox v1.19.4 (2023-05-26 10:21:37 CST)"),
    # source: https://www.zyxel.com/global/en/support/download?model=nas326 (521AAZF18C0.pdf, "June 17, 2024",
    #   the last firmware listed)
    # unverified: the kernel build time (the image is packed), OpenSSH as in AAZF.14, BusyBox
    _firmware("5.21(AAZF.18)C0", "2024-06-17", None, *_ZYXEL_SSH, "3.10.39",
              "#1 Tue Jun 11 16:42:51 CST 2024", "root@twsvnnt01652-01", _ZYXEL_GCC, "V5.21(AAZF.18)C0", "5.21",
              busybox="BusyBox v1.19.4 (2024-06-11 16:53:20 CST)"),
)

# MT7621A routers with NOR flash (MIPS 1004Kc, 2 cores x 2 VPEs, 880 MHz, 128 MB); hardware.disk_kb is the
#   firmware partition; unverified: MemTotal, BogoMIPS, the MTD numbers of the rootfs split
# source: https://openwrt.org/toh/xiaomi/mi_router_4a_gigabit_edition
# source: https://openwrt.org/toh/tp-link/archer_c6_v3
_MT7621 = "MediaTek MT7621 ver:1 eco:3"
XIAOMI_4A = "Xiaomi Mi Router 4A Gigabit Edition"
ARCHER_C6 = "TP-Link Archer C6 v3"
OPENWRT_HARDWARE = (
    Hardware(cpus=4, cpu_model="MIPS 1004Kc V2.15", mem_kb=124108, disk_kb=15104, board=_MT7621, machine=XIAOMI_4A),
    Hardware(cpus=4, cpu_model="MIPS 1004Kc V2.15", mem_kb=123844, disk_kb=7680, board=_MT7621, machine=ARCHER_C6),
)
_OPENWRT_DEVICES = {"rootfs_mtd": {XIAOMI_4A: 9, ARCHER_C6: 3},
                    "board_names": {XIAOMI_4A: "xiaomi,mi-router-4a-gigabit", ARCHER_C6: "tplink,archer-c6-v3"}}


def _openwrt(version, revision, released, kernel, uts, compiler, dropbear, busybox, kernel_kb, rootfs_kb):
    # the last release of an end-of-life branch: no next firmware, it stays
    return _firmware(version, released, None, "SSH-2.0-dropbear", f"Dropbear v{dropbear}", kernel, uts,
                     "builder@buildhost", compiler, version, version, revision=revision, target="ramips/mt7621",
                     pkg_arch="mipsel_24kc", busybox=busybox, kernel_kb=kernel_kb, rootfs_kb=rootfs_kb,
                     **_OPENWRT_DEVICES)


# kernel, BusyBox and the dropbear package from the ramips/mt7621 sysupgrade images; dropbear of OpenWrt
#   sends no version in its banner; df of /rom: the squashfs in 256 KiB blocks, kernel_kb the uImage
OPENWRT = (
    # source: https://openwrt.org/releases/21.02/notes-21.02.7 (2023-05-01, r16847-f8282da11e, Linux 5.4.238)
    _openwrt("21.02.7", "r16847-f8282da11e", "2023-05-01", "5.4.238", "#0 SMP Mon Apr 17 13:15:36 2023",
             "gcc version 8.4.0 (OpenWrt GCC 8.4.0 r16847-f8282da11e)", "2020.81",
             "BusyBox v1.33.2 (2023-04-17 13:15:36 UTC)", 2330, 3328),
    # source: https://openwrt.org/releases/22.03/notes-22.03.7 (2024-07-25, r20341-591b7e93d3, Linux 5.10.221)
    _openwrt("22.03.7", "r20341-591b7e93d3", "2024-07-25", "5.10.221", "#0 SMP Mon Jul 15 22:25:54 2024",
             "mipsel-openwrt-linux-musl-gcc (OpenWrt GCC 11.2.0 r20341-591b7e93d3) 11.2.0, GNU ld (GNU Binutils) "
             "2.37", "2022.82", "BusyBox v1.35.0 (2024-07-15 22:25:54 UTC)", 2652, 3840),
)

X86 = ("linux-x64-lsb", "x86_64")
# TS-451+ (Celeron J1900, 4 cores, 2 GB) and TS-464 (Celeron N5105, 4 cores, 8 GB); unverified: MemTotal
QNAP_HARDWARE = (
    Hardware(cpus=4, cpu_model="Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz", mem_kb=1945988, disk_kb=3840539624),
    Hardware(cpus=4, cpu_model="Intel(R) Celeron(R) N5105 @ 2.00GHz", mem_kb=7881640, disk_kb=7691397408),
)


def _qts(version, number, build, released, superseded, uts, openssh, openssl):
    # unverified: uname -v (built the night of the build date), builder and compiler of the QNAP kernel
    #   (gcc 8.4.0, binutils assumed), the OpenSSH / OpenSSL of each build; a 5.10 kernel names the
    #   compiler as `cc --version` and the linker (the format of Linux 5.8 and later); the BusyBox of
    #   QTS (1.24.1, built the night of the build like the kernel)
    clock = re.search(r"\d\d:\d\d:\d\d", uts).group()
    return _firmware(f"{version}.{number}", released, superseded, f"SSH-2.0-OpenSSH_{openssh.split('p')[0]}",
                     f"OpenSSH_{openssh}, {openssl}", "5.10.60-qnap", uts, "root@U16BuildServer141",
                     "gcc (GCC) 8.4.0, GNU ld (GNU Binutils) 2.32", f"{version}.{number} build {build}", version,
                     build=build, number=number,
                     busybox=f"BusyBox v1.24.1 ({build[:4]}-{build[4:6]}-{build[6:]} {clock} CST)")


# QTS 5.2 builds of the release notes; superseded by the next build of the notes
QNAP = (
    # source: https://www.qnap.com/en/release-notes/qts (QTS 5.2.3.3006 build 20250108, 2025-01-10)
    _qts("5.2.3", "3006", "20250108", "2025-01-10", "2025-03-17", "#1 SMP Wed Jan  8 02:31:17 CST 2025", "9.8p1",
         "OpenSSL 3.0.15 3 Sep 2024"),
    # source: https://www.qnap.com/en/release-notes/qts (QTS 5.2.6.3229 build 20250818, 2025-08-21)
    _qts("5.2.6", "3229", "20250818", "2025-08-21", "2025-09-25", "#1 SMP Mon Aug 18 02:27:40 CST 2025", "9.9p2",
         "OpenSSL 3.0.16 11 Feb 2025"),
    # source: https://www.qnap.com/en/release-notes/qts (QTS 5.2.8.3359 build 20251225, 2026-01-07)
    _qts("5.2.8", "3359", "20251225", "2026-01-07", "2026-03-04", "#1 SMP Thu Dec 25 02:19:52 CST 2025", "9.9p2",
         "OpenSSL 3.0.17 1 Jul 2025"),
)

# DS1618+ (Atom C3538) and DS920+ (Celeron J4125), 4 GB each; the provider names the platform for the
#   host names (DS1618 / DS920 only on their CPU); unverified: MemTotal
SYNOLOGY_HARDWARE = (
    Hardware(cpus=4, cpu_model="Intel(R) Atom(TM) CPU C3538 @ 2.10GHz", mem_kb=3933060, disk_kb=11231231488,
             provider="denverton"),
    Hardware(cpus=4, cpu_model="Intel(R) Celeron(R) J4125 CPU @ 2.00GHz", mem_kb=3765644, disk_kb=5615615744,
             provider="geminilake"),
)


def _dsm(version, build, smallfix, released, superseded, uts, builddate, openssl):
    # unverified: uname -v, builder and compiler of the kernel, builddate, the OpenSSL of ssh -V
    #   (DSM 7.2 moved to OpenSSL 3; OpenSSH 8.2p1 of DSM 7)
    return _firmware(f"{version}-{build} Update {smallfix}", released, superseded, "SSH-2.0-OpenSSH_8.2",
                     f"OpenSSH_8.2p1, {openssl}", "4.4.302+", uts, "root@build13", "gcc version 12.2.0 (GCC) ",
                     version, version.rsplit(".", 1)[0] if version.count(".") == 2 else version, build=build,
                     smallfix=smallfix, builddate=builddate)


# DSM updates of the release notes of the DS920+ / DS1618+; superseded: the next DSM a box of that level
#   is offered (7.2.1 Update 5 -> 7.2.2-72803, 7.2.2 Update 4 -> 7.3)
SYNOLOGY = (
    # source: https://www.synology.com/en-global/releaseNote/DSM?model=DS920%2B (7.2.1-69057 Update 5, 2024-04-08)
    _dsm("7.2.1", "69057", "5", "2024-04-08", "2024-08-26", "#69057 SMP Wed Mar 27 18:12:44 CST 2024",
         "2024/03/27", "OpenSSL 3.0.13 30 Jan 2024"),
    # source: https://www.synology.com/en-global/releaseNote/DSM?model=DS920%2B (7.2.2-72806 Update 3, 2025-02-04)
    _dsm("7.2.2", "72806", "3", "2025-02-04", "2025-07-24", "#72806 SMP Tue Jan 21 15:40:31 CST 2025",
         "2025/01/21", "OpenSSL 3.0.15 3 Sep 2024"),
    # source: https://www.synology.com/en-global/releaseNote/DSM?model=DS920%2B (7.2.2-72806 Update 4, 2025-07-24)
    _dsm("7.2.2", "72806", "4", "2025-07-24", "2025-10-08", "#72806 SMP Tue Jul  8 16:05:22 CST 2025",
         "2025/07/08", "OpenSSL 3.0.16 11 Feb 2025"),
)

# EdgeRouter X: MT7621AT (2 cores x 2 VPEs), 256 MB, 256 MB NAND (UBI); unverified: MemTotal, the machine
#   name of the vendor kernel, the UBI volume size; df of /root.ro: the squashfs of the image in 128 KiB blocks
EDGEOS_HARDWARE = (Hardware(cpus=4, cpu_model="MIPS 1004Kc V2.15", mem_kb=249832, disk_kb=196608, board=_MT7621,
                            machine="UBNT_E50"),)
# kernel, BusyBox, OpenSSH (Debian 9 package, linked with OpenSSL 1.0.2u), dpkg and apt from the images
_EDGEOS_SSH = ("SSH-2.0-OpenSSH_7.4p1 Debian-10+deb9u7", "OpenSSH_7.4p1 Debian-10+deb9u7, OpenSSL 1.0.2u  20 Dec 2019")
_EDGEOS_GCC = "gcc version 6.3.0 20170516 (Debian 6.3.0-18) "


def _edgeos(hotfix, image, released, superseded, uts, builder, busybox, squashfs_kb):
    return _firmware(f"2.0.9-hotfix.{hotfix}", released, superseded, *_EDGEOS_SSH, "4.14.54-UBNT", uts, builder,
                     _EDGEOS_GCC, f"v2.0.9-hotfix.{hotfix}", "9", packages={"dpkg": "1.18.25", "apt": "1.4.10"},
                     image=image, busybox=busybox, squashfs_kb=squashfs_kb, deb_arch="mipsel")


EDGEOS = (
    # source: https://dl.ui.com/firmwares/edgemax/v2.0.9-hotfix.2/ER-e50.v2.0.9-hotfix.2.5402463.tar
    # unverified: the release day (a week after the build), superseded by hotfix.4 (its build date)
    _edgeos(2, "v2.0.9-hotfix.2.5402463.210511.1317", "2021-05-18", "2022-06-30",
            "#1 SMP Tue May 11 13:23:28 UTC 2021", "ubnt@5dd49e7e3fb9", "BusyBox v1.27.2 (2021-05-11 12:39:02 UTC)",
            69888),
    # source: https://dl.ui.com/firmwares/edgemax/v2.0.9-hotfix.7/ER-e50.v2.0.9-hotfix.7.5622731.tar (the last
    #   EdgeOS of the ER-X); unverified: the release day
    _edgeos(7, "v2.0.9-hotfix.7.5622731.230615.0857", "2023-06-20", None, "#1 SMP Thu Jun 15 09:00:10 UTC 2023",
            "ubnt@a704596799ed", "BusyBox v1.27.2 (2023-06-15 08:27:54 UTC)", 70144),
)

# IP camera in the style of Hikvision's DS-2CD2xxx: one Cortex-A7 (HiSilicon class SoC), 64 or 128 MB for
#   Linux (mem= of the command line, the rest for the video buffers), a dav partition with the application;
# unverified: all values of the camera (kernel, toolchain, BusyBox, dropbear and the firmware strings follow
#   the version pairs of the brief, not one image), the dates of the firmware
IPCAM_HARDWARE = (
    Hardware(cpus=1, cpu_model="ARMv7 Processor rev 5 (v7l)", mem_kb=60268, disk_kb=12288,
             board="Generic DT based system"),
    Hardware(cpus=1, cpu_model="ARMv7 Processor rev 5 (v7l)", mem_kb=119744, disk_kb=16384,
             board="Generic DT based system"),
)
_IPCAM_GCC = "gcc version 4.9.4 20150629 (prerelease) (Hisilicon_v500_20170922) "
IPCAM = (
    # source: https://www.hikvision.com/en/support/download/firmware/ (firmware of the DS-2CD2x43G0 series)
    _firmware("V5.5.82_190909", "2019-10-15", "2021-03-01", "SSH-2.0-dropbear_2015.71", "Dropbear sshd 2015.71",
              "3.18.20", "#1 SMP Mon Sep  9 17:05:12 CST 2019", "root@localhost.localdomain", _IPCAM_GCC,
              "V5.5.82", "5.5", firmware_version="V5.5.82 build 190909",
              busybox="BusyBox v1.19.4 (2019-09-09 17:21:46 CST)"),
    # source: https://www.hikvision.com/en/support/download/firmware/ (firmware of the DS-2CD2x43G2 series)
    _firmware("V5.7.3_220112", "2022-02-15", None, "SSH-2.0-dropbear_2016.74", "Dropbear sshd 2016.74",
              "3.18.20", "#1 SMP Wed Jan 12 11:38:57 CST 2022", "root@localhost.localdomain", _IPCAM_GCC,
              "V5.7.3", "5.7", firmware_version="V5.7.3 build 220112",
              busybox="BusyBox v1.26.2 (2022-01-12 11:52:03 CST)"),
)


def _server(pid, family, os_id, user, releases, hardware, hostnames, builder, summary, managers=APT,
            arch=("linux-x64-lsb", "x86_64"), tokens=(), max_install_days=900, prompt_style="debian"):
    return Persona(
        id=pid, family=family, kind="server", os_id=os_id,
        user=user, uid=1000, gid=1000, shell="/bin/bash",
        arch=arch[0], hardware_platform=arch[1], operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=managers,
        releases=releases, hardware=hardware, hostnames=hostnames,
        builder=builder, summary=summary, tokens=tokens, max_install_days=max_install_days,
        prompt_style=prompt_style,
    )


PERSONAS = (
    _server("ubuntu-noble", "ubuntu", "ubuntu", "ubuntu", NOBLE, (AWS_2, AWS_4, KVM_4, DO_2),
            # the AWS default VPC, a hoster's VPS, a DigitalOcean droplet named after its plan
            ("aws:ip-172-31-{n}-{n}", "kvm:vps-{hex6}", "kvm:{word}-{nn}", "do:ubuntu-s-{cpus}vcpu-{gib}gb-fra1-{nn}"),
            build_ubuntu, "Ubuntu 24.04 cloud VPS, OpenSSH 9.6p1"),
    _server("ubuntu-jammy", "ubuntu", "ubuntu", "ubuntu", JAMMY, (KVM_1, KVM_2, KVM_4),
            ("srv{nn}", "{word}{nn}"),
            build_ubuntu, "Ubuntu 22.04 server, OpenSSH 8.9p1"),
    _server("debian-trixie", "debian", "debian", "debian", TRIXIE, (KVM_2, KVM_4, KVM_8),
            ("debian-{hex6}", "{word}-{nn}", "vm{nn}"),
            build_debian, "Debian 13 server, OpenSSH 10.0p2"),
    _server("debian-bookworm", "debian", "debian", "debian", BOOKWORM, (KVM_1, KVM_2, KVM_4),
            ("db12-{word}{nn}", "{word}-prod-{nn}"),
            build_debian, "Debian 12 server, OpenSSH 9.2p1 at older and current patch levels"),
    _server("rhel-9", "rhel", "rhel", "cloud-user", RHEL9, (KVM_2, KVM_4, KVM_8),
            ("rhel9-{word}{nn}", "{word}{nn}.corp.local"),
            build_rhel, "RHEL 9 cloud image, OpenSSH 8.7p1", managers=DNF, prompt_style="redhat"),
    _server("centos-7", "centos", "centos", "centos", CENTOS7, (KVM_1, KVM_2, KVM_4),
            ("localhost.localdomain", "cent7-{word}{nn}", "{word}{nn}"),
            # an EOL box forgotten for years: installed up to ten years ago
            build_centos, "EOL CentOS 7, OpenSSH 7.4", managers=YUM, max_install_days=3650,
            prompt_style="redhat"),
    _server("raspios", "raspios", "debian", "pi", RASPIOS, PI_HARDWARE,
            ("raspberrypi", "pi{nn}", "pihole"),
            build_raspios, "Raspberry Pi with the default user pi", arch=("linux-aarch64-lsb", "aarch64")),
    _server("gpu-ubuntu", "ubuntu", "ubuntu", "ubuntu", GPU_RELEASES, GPU_HARDWARE,
            # DGX and A100 names only on the 8x A100 box
            ("dgx:dgx{nn}", "dgx:a100-worker-{nn}", "gpu-node-{nn}", "gpu:ml-{hex6}", "gpu:{gpu}-worker-{nn}"),
            build_gpu_ubuntu, "GPU server with NVIDIA driver, Ollama and Jupyter",
            tokens=(("hf", "hf"), ("openai", "openai"))),
    _device("dlink-dir859", "dlink", "dlink", "admin", "/bin/sh", MIPS_BE, "dropbear", (), DLINK, DLINK_HARDWARE,
            ("dlinkrouter", "DIR-859"), build_dlink, "D-Link DIR-859 router, discontinued, old firmware",
            remove_paths=VENDOR_ONLY, max_install_days=3650),
    _device("tplink-wr841n", "tplink", "tplink", "admin", "/bin/sh", MIPS, "dropbear", (), TPLINK, TPLINK_HARDWARE,
            ("TL-WR841N",), build_tplink, "TP-Link TL-WR841N v14 router, firmware not updated",
            remove_paths=VENDOR_ONLY, max_install_days=1500),
    _device("zyxel-nas326", "zyxel", "zyxel", "admin", "/bin/sh", ("linux-arm-lsb", "armv7l"), "openssh", (),
            ZYXEL, ZYXEL_HARDWARE, ("NAS326",), build_zyxel, "Zyxel NAS326, end of support",
            remove_paths=VENDOR_ONLY, max_install_days=2190),
    _device("openwrt", "openwrt", "openwrt", "root", "/bin/ash", MIPS, "dropbear-openwrt", ("opkg",), OPENWRT,
            OPENWRT_HARDWARE, ("OpenWrt",), build_openwrt, "OpenWrt router on an end-of-life release", uid=0,
            remove_paths=("home/root",) + VENDOR_ONLY),
    _device("qnap-qts", "qnap", "qnap", "admin", "/bin/sh", X86, "openssh", ("qpkg_cli",), QNAP, QNAP_HARDWARE,
            ("NAS{HEX6}",), build_qnap, "QNAP NAS on QTS 5.2, updates not installed",
            remove_paths=VENDOR_ONLY),
    _device("synology-dsm", "synology", "synology", "admin", "/bin/sh", X86, "openssh", ("synopkg",), SYNOLOGY,
            SYNOLOGY_HARDWARE, ("DiskStation", "denverton:DS1618", "geminilake:DS920"), build_synology,
            "Synology DiskStation on DSM 7.2, updates not installed", uid=1024,
            remove_paths=VENDOR_ONLY),
    _device("ubiquiti-edgerouter-x", "ubiquiti", "debian", "ubnt", "/bin/vbash", MIPS, "openssh", ("apt-get", "dpkg"),
            EDGEOS, EDGEOS_HARDWARE, ("ubnt", "EdgeRouter-X-5-Port"), build_ubiquiti,
            "Ubiquiti EdgeRouter X on EdgeOS 2.0, end of updates",
            remove_paths=("etc/debian_version",), max_install_days=2190),
    _device("ipcam", "ipcam", "ipcam", "admin", "/bin/sh", ("linux-arm-lsb", "armv7l"), "dropbear", (), IPCAM,
            IPCAM_HARDWARE, ("IPCamera", "HIKVISION", "cam-{nn}"), build_ipcam,
            "IP camera (Hikvision-style), old firmware", remove_paths=VENDOR_ONLY, max_install_days=3650),
)


def ids():
    return [p.id for p in PERSONAS]


def by_id(pid):
    for p in PERSONAS:
        if p.id == pid:
            return p
    raise PersonaError(f'unknown persona "{pid}", allowed: {" ".join(ids())}')
