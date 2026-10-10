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

Devices: the values of the generator before the persona library (one release,
one hardware entry and one host name each); Task 4 renews them.
"""
from dataclasses import replace

from . import PersonaError
from .devices import build_iot_nas, build_iot_router, build_openwrt, build_qnap, build_synology, build_ubiquiti
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
# single core MIPS SoC with 64 MB as on small routers
MIPS_ROUTER = Hardware(cpus=1, cpu_model="MIPS 24Kc V7.4", mem_kb=65536, disk_kb=8192)


# --- release helpers ----------------------------------------------------------------

def _chain(*releases):
    """A pool of patch levels in update order: each one is superseded on the day
    the next one came out (the logs of a system never pull its versions later)."""
    out = []
    for i, r in enumerate(releases):
        extra = {k: v for k, v in r.extra.items() if k != "superseded"}
        if i + 1 < len(releases):
            assert releases[i + 1].extra["updated"] > r.extra["updated"], r.label
            extra["superseded"] = releases[i + 1].extra["updated"]
        out.append(replace(r, extra=extra))
    return tuple(out)


def _medium(release):
    """The patch level of an installation image (point release)."""
    return replace(release, extra={**release.extra, "medium": True})


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
_TRIXIE_PKGS = {"apt": "3.0.3", "dpkg": "1.22.21", "htop": "3.4.1-5", "git": "1:2.47.3-0+deb13u1",
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
        packages={"dnf": "4.14.0", "rpm": "4.16.1.3", "openssh-server": openssh, "tmux": "3.2a-5.el9",
                  **packages},
        extra={"kernel_builder": builder, "kernel_compiler": compiler, "first_install": "2022-05-17",
               "updated": updated,
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
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.6",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    _ubuntu("2026-02-25", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.14", "1:9.6p1-3ubuntu13.13", "6.8.0-101",
            "#101-Ubuntu SMP PREEMPT_DYNAMIC Mon Feb  9 10:15:05 UTC 2026", "lcy02-amd64-051", _NOBLE_GCC,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.7",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    _ubuntu("2026-05-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.16", "1:9.6p1-3ubuntu13.15", "6.8.0-117",
            "#117-Ubuntu SMP PREEMPT_DYNAMIC Tue May  5 19:26:24 UTC 2026", "lcy02-amd64-102", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.9",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.13", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}),
    # 13.17 was only in -proposed
    _ubuntu("2026-07-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.18", "1:9.6p1-3ubuntu13.16", "6.8.0-136",
            "#136-Ubuntu SMP PREEMPT_DYNAMIC Wed Jul  1 21:53:05 UTC 2026", "lcy02-amd64-041", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.11",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.18", "htop": "3.3.0-4build1",
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
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.5", "curl": "8.5.0-2ubuntu10.6",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.9", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}, flavor="aws", providers=("aws",)),
    _ubuntu("2026-07-20", "noble", "24.04.4 LTS", "1:9.6p1-3ubuntu13.18", "1:9.6p1-3ubuntu13.16", "6.17.0-1019",
            "#19~24.04.1-Ubuntu SMP Tue Jun 23 19:03:52 UTC 2026", "lcy02-amd64-070", _NOBLE_GCC_1,
            _NOBLE_SSL, {"apt": "2.8.3", "dpkg": "1.22.6ubuntu6.6", "curl": "8.5.0-2ubuntu10.11",
                         "git": "1:2.43.0-1ubuntu7.3", "vim": "2:9.1.0016-1ubuntu7.18", "htop": "3.3.0-4build1",
                         "net-tools": "2.10-0.1ubuntu4.4"}, flavor="aws", providers=("aws",)),
)
NOBLE = _chain(*_NOBLE_LEVELS[1:]) + _chain(*_NOBLE_AWS_LEVELS)

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
JAMMY = _chain(*_JAMMY_LEVELS)

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
TRIXIE = _chain(_medium(_TRIXIE_LEVELS[0]), _medium(_TRIXIE_LEVELS[1]), *_TRIXIE_LEVELS[2:])

# --- Debian 12 ------------------------------------------------------------------------
# openssh: https://metadata.ftp-master.debian.org/changelogs/main/o/openssh/oldstable_changelog
#   regreSSHion (CVE-2024-6387) fixed in 1:9.2p1-2+deb12u3, https://security-tracker.debian.org/tracker/CVE-2024-6387
# kernel: https://metadata.ftp-master.debian.org/changelogs/main/l/linux/oldstable_changelog ("Bump ABI to N");
#   6.1.0-47 / -50 from https://deb.debian.org/debian/pool/main/l/linux-signed-amd64/
# point releases: https://metadata.ftp-master.debian.org/changelogs/main/b/base-files/oldstable_changelog
# OpenSSL dates: VERSION.dat of the openssl-3.0.x tags on github.com/openssl/openssl
_BOOKWORM_LEVELS = (
    # 12.2, before the regreSSHion fix
    _debian("2023-10-10", "bookworm", "12.2", "9.2p1", "1:9.2p1-2+deb12u1", "1:9.2p1-2",
            "6.1.0-13-amd64", "6.1.55-1", "2023-09-29",
            _BOOKWORM_GCC, "OpenSSL 3.0.11 19 Sep 2023",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u4", "git": "1:2.39.2-1.1", "vim": "2:9.0.1378-2",
             "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.5, before the regreSSHion fix
    _debian("2024-02-12", "bookworm", "12.5", "9.2p1", "1:9.2p1-2+deb12u2", "1:9.2p1-2+deb12u1",
            "6.1.0-18-amd64", "6.1.76-1", "2024-02-01",
            _BOOKWORM_GCC, "OpenSSL 3.0.11 19 Sep 2023",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u5", "git": "1:2.39.2-1.1", "vim": "2:9.0.1378-2",
             "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.11
    _debian("2025-05-20", "bookworm", "12.11", "9.2p1", "1:9.2p1-2+deb12u6", "1:9.2p1-2+deb12u5",
            "6.1.0-36-amd64", "6.1.139-1", "2025-05-18",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.16 11 Feb 2025",
            {**_BOOKWORM_PKGS, "curl": "7.88.1-10+deb12u12", "git": "1:2.39.5-0+deb12u2",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u1"}),
    # 12.14
    _debian("2026-05-12", "bookworm", "12.14", "9.2p1", "1:9.2p1-2+deb12u10", "1:9.2p1-2+deb12u9",
            "6.1.0-47-amd64", "6.1.170-3", "2026-05-08",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.20 7 Apr 2026",
            {**_BOOKWORM_PKGS, "dpkg": "1.21.23", "curl": "7.88.1-10+deb12u15", "git": "1:2.39.5-0+deb12u3",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u4"}),
    # 12.15
    _debian("2026-07-06", "bookworm", "12.15", "9.2p1", "1:9.2p1-2+deb12u10", "1:9.2p1-2+deb12u9",
            "6.1.0-50-amd64", "6.1.176-1", "2026-07-02",
            _BOOKWORM_GCC_U1, "OpenSSL 3.0.20 7 Apr 2026",
            {**_BOOKWORM_PKGS, "dpkg": "1.21.23", "curl": "7.88.1-10+deb12u15", "git": "1:2.39.5-0+deb12u3",
             "vim": "2:9.0.1378-2+deb12u2", "sudo": "1.9.13p3-1+deb12u4"}),
)
# 12.2 and 12.5 are the versions of their installation images: the regreSSHion bait stays installable
BOOKWORM = _chain(_medium(_BOOKWORM_LEVELS[0]), _medium(_BOOKWORM_LEVELS[1]), *_BOOKWORM_LEVELS[2:])

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
          {"curl": "7.76.1-29.el9_4.1", "git": "2.43.5-1.el9_4", "vim-enhanced": "2:8.2.2637-20.el9_1"}),
    # 9.5 GA kernel; uname -v verbatim from https://www.spec.org/cpu2017/results/res2025q2/cpu2017-20250529-48392.html
    # unverified: builder host and compiler (gcc 11.5.0 of RHEL 9.5)
    _rhel("2024-11-20", 5, "5.14.0-503.11.1", "#1 SMP PREEMPT_DYNAMIC Mon Sep 30 11:54:45 EDT 2024",
          "mockbuild@x86-64-01.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-2), GNU ld version 2.35.2-54.el9", "8.7p1-43.el9",
          "8.7p1-38.el9_4.4", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9", "git": "2.43.5-1.el9_4", "vim-enhanced": "2:8.2.2637-21.el9"}),
    # 9.6, kernel of 2025-06; unverified: the time of uname -v, builder host and compiler
    _rhel("2025-06-05", 6, "5.14.0-570.18.1", "#1 SMP PREEMPT_DYNAMIC Thu May 15 08:45:21 EDT 2025",
          "mockbuild@x86-64-02.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-5), GNU ld version 2.35.2-63.el9", "8.7p1-45.el9",
          "8.7p1-43.el9", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9", "git": "2.47.1-2.el9_6", "vim-enhanced": "2:8.2.2637-22.el9_6"}),
    # 9.6 EUS, kernel of 2026-08; uname -v verbatim from https://github.com/uxlfoundation/oneDNN/issues/6151
    # unverified: openssh / curl errata of 9.6 EUS after 2025, builder host and compiler
    _rhel("2026-08-25", 6, "5.14.0-570.135.1", "#1 SMP PREEMPT_DYNAMIC Wed Aug 12 22:16:32 EDT 2026",
          "mockbuild@x86-64-02.build.eng.rdu2.redhat.com",
          "gcc (GCC) 11.5.0 20240719 (Red Hat 11.5.0-5), GNU ld version 2.35.2-63.el9", "8.7p1-45.el9",
          "8.7p1-43.el9", "OpenSSL 3.2.2 4 Jun 2024",
          {"curl": "7.76.1-31.el9_6.1", "git": "2.47.3-1.el9_6", "vim-enhanced": "2:8.2.2637-22.el9_6.1"}),
)
# in update order; 9.5 GA is the 9.5 installation image
RHEL9 = _chain(_medium(_RHEL9_LEVELS[1]), _RHEL9_LEVELS[0], *_RHEL9_LEVELS[2:])

# --- CentOS 7 -------------------------------------------------------------------------
# Packages and their dates: https://vault.centos.org/7.9.2009/updates/x86_64/Packages/ and .../os/x86_64/Packages/
# 3.10.0-1160.el7 uname -v verbatim from a boot log ("Mon Oct 19 16:18:59 UTC 2020", search result of
#   openbenchmarking.org); unverified: the uname -v times of the updates (vault file time less an hour)
_CENTOS7_LEVELS = (
    # 7.9.2009 as installed, never updated
    _centos("2020-11-12", "1160", "#1 SMP Mon Oct 19 16:18:59 UTC 2020", "7.4p1-21.el7", None,
            {"rpm": "4.11.3-45.el7", "curl": "7.29.0-59.el7", "git": "1.8.3.1-23.el7_8",
             "vim-enhanced": "2:7.4.629-7.el7"}),
    _centos("2023-03-15", "1160.88.1", "#1 SMP Tue Mar  7 15:41:52 UTC 2023", "7.4p1-22.el7_9", "7.4p1-21.el7",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.1", "git": "1.8.3.1-24.el7_9",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
    _centos("2023-12-13", "1160.105.1", "#1 SMP Thu Dec  7 15:39:45 UTC 2023", "7.4p1-23.el7_9", "7.4p1-22.el7_9",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.1", "git": "1.8.3.1-25.el7_9",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
    # the last kernel before the end of life on 2024-06-30
    _centos("2024-06-10", "1160.119.1", "#1 SMP Tue Jun  4 14:43:51 UTC 2024", "7.4p1-23.el7_9", "7.4p1-22.el7_9",
            {"rpm": "4.11.3-48.el7_9", "curl": "7.29.0-59.el7_9.2", "git": "1.8.3.1-25.el7_9",
             "vim-enhanced": "2:7.4.629-8.el7_9"}),
)
# 7.9.2009 as installed is the image; a later install would get the newest updates from the vault
CENTOS7 = _chain(_medium(_CENTOS7_LEVELS[0]), *_CENTOS7_LEVELS[1:])

# --- Raspberry Pi OS (Bookworm, arm64) ------------------------------------------------
# kernels and their changelog dates: debian.tar.xz of https://archive.raspberrypi.com/debian/pool/main/l/linux/
#   uname -v is "Debian <version> (<changelog date>)" (debian/rules.real), built by the Maintainer serge@raspberrypi.com
# image dates: https://downloads.raspberrypi.com/raspios_arm64/release_notes.txt, pi-gen commit = the tag
#   <date>-raspios-bookworm-arm64 of https://github.com/RPi-Distro/pi-gen; rpi-issue format: pi-gen scripts/common
# Debian userland: the bookworm changelogs (as for debian-bookworm) at the date of each update
# unverified: the compiler of the Pi kernel builds (the Debian gcc-12 of bookworm assumed)
_PI_GCC = "aarch64-linux-gnu-gcc-12 (Debian 12.2.0-14) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
_PI_GCC_U1 = "aarch64-linux-gnu-gcc-12 (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40"
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
RASPIOS = _chain(_medium(_RASPIOS_LEVELS[0]), *_RASPIOS_LEVELS[1:])

# --- GPU server (Ubuntu + NVIDIA) -----------------------------------------------------
# Drivers: Launchpad nvidia-graphics-drivers-550 / -570 (jammy, noble); CUDA 12.4 / 12.8 is the CUDA
#   version of the 550 / 570 branches (https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-570-211-01/)
# /proc/driver/nvidia/version format: "NVRM version: NVIDIA UNIX x86_64 Kernel Module  <ver>  <date>"
# unverified: the build dates in it (the file dates of https://download.nvidia.com/XFree86/Linux-x86_64/<ver>/)


def _gpu(base, driver, cuda, built, gcc):
    return replace(base, label=f"{base.label} nvidia-{driver}",
                   extra={**base.extra, "nvidia_driver": driver, "cuda": cuda, "nvidia_built": built,
                          "nvidia_gcc": gcc, "providers": ("dgx", "gpu")})


GPU_RELEASES = _chain(
    _gpu(_JAMMY_LEVELS[1], "550.163.01", "12.4", "Wed Apr  9 19:58:21 UTC 2025",
         "gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.2)"),
    _gpu(_JAMMY_LEVELS[2], "570.211.01", "12.8", "Tue Dec  9 20:31:47 UTC 2025",
         "gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3)"),
) + _chain(
    _gpu(_NOBLE_LEVELS[1], "570.195.03", "12.8", "Tue Sep 23 16:12:09 UTC 2025",
         "gcc version 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04)"),
    _gpu(_NOBLE_LEVELS[3], "570.211.01", "12.8", "Tue Dec  9 20:31:47 UTC 2025",
         "gcc version 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1)"),
)


def _server(pid, family, os_id, user, releases, hardware, hostnames, builder, vulnerability, managers=APT,
            arch=("linux-x64-lsb", "x86_64"), tokens=()):
    return Persona(
        id=pid, family=family, kind="server", os_id=os_id,
        user=user, uid=1000, gid=1000, shell="/bin/bash",
        arch=arch[0], hardware_platform=arch[1], operating_system="GNU/Linux",
        ssh_profile="openssh", package_managers=managers,
        releases=releases, hardware=hardware, hostnames=hostnames,
        builder=builder, vulnerability=vulnerability, tokens=tokens,
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
            build_debian, "Debian 12 OpenSSH 9.2p1 before the regreSSHion fix (CVE-2024-6387)"),
    _server("rhel-9", "rhel", "rhel", "cloud-user", RHEL9, (KVM_2, KVM_4, KVM_8),
            ("rhel9-{word}{nn}", "{word}{nn}.corp.local"),
            build_rhel, "RHEL 9 cloud image, OpenSSH 8.7p1", managers=DNF),
    _server("centos-7", "centos", "centos", "centos", CENTOS7, (KVM_1, KVM_2, KVM_4),
            ("localhost.localdomain", "cent7-{word}{nn}", "{word}{nn}"),
            build_centos, "EOL CentOS 7, OpenSSH 7.4", managers=YUM),
    _server("raspios", "raspios", "debian", "pi", RASPIOS, PI_HARDWARE,
            ("raspberrypi", "pi{nn}", "pihole"),
            build_raspios, "Raspberry Pi with the default user pi", arch=("linux-aarch64-lsb", "aarch64")),
    _server("gpu-ubuntu", "ubuntu", "ubuntu", "ubuntu", GPU_RELEASES, GPU_HARDWARE,
            # DGX and A100 names only on the 8x A100 box
            ("dgx:dgx{nn}", "dgx:a100-worker-{nn}", "gpu-node-{nn}", "gpu:ml-{hex6}", "gpu:{gpu}-worker-{nn}"),
            build_gpu_ubuntu, "GPU server with NVIDIA driver, Ollama and Jupyter",
            tokens=(("hf", "hf"), ("openai", "openai"))),
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
