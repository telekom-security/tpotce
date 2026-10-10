"""The data a persona is made of. Everything that varies per start is in a pool
(releases, hardware, hostnames) and is drawn into an Identity."""
from dataclasses import dataclass, field
from typing import Callable

# how the shell prompt looks ([shell] prompt_style, read by the patched showPrompt of Cowrie):
#   debian  user@host:~/dir$   Cowrie's own prompt, bash PS1 of Debian / Ubuntu, kept for the devices
#   redhat  [user@host dir]$   bash PS1 of RHEL / CentOS (/etc/bashrc): short host name, basename of the cwd
PROMPT_STYLES = ("debian", "redhat")


@dataclass(frozen=True)
class Release:
    label: str
    ssh_banner: str          # what the SSH server sends, "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5"
    ssh_version: str         # what `ssh -V` prints, must name the same product and version
    kernel_version: str      # uname -r
    kernel_build: str        # uname -v
    os_version: str          # PRETTY_NAME part, "24.04.3 LTS"
    os_version_id: str       # VERSION_ID, "24.04"
    packages: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Hardware:
    cpus: int
    cpu_model: str
    mem_kb: int
    disk_kb: int
    gpus: tuple = ()
    gpu_mem_mib: int = 0
    board: str = ""          # Raspberry Pi model, device SoC (ARM Hardware, MIPS system type); empty for VMs
    machine: str = ""        # the board a MIPS kernel names after the SoC (cpuinfo "machine")
    provider: str = ""       # where it runs (aws, kvm, do, pi, dgx, gpu); picks host names, releases, lspci, disks


@dataclass
class Content:
    files: dict
    txtcmds: dict
    processes: list
    ctimes: dict = field(default_factory=dict)  # rel path -> ctime, for files whose date shows (logs, shadow)


@dataclass(frozen=True)
class Persona:
    id: str
    family: str
    kind: str
    os_id: str
    user: str
    uid: int
    gid: int
    shell: str
    arch: str
    hardware_platform: str
    operating_system: str
    ssh_profile: str
    package_managers: tuple
    releases: tuple
    hardware: tuple
    hostnames: tuple         # patterns; "aws:ip-172-31-{n}-{n}" only for hardware of that provider
    builder: Callable
    vulnerability: str
    remove_paths: tuple = ()
    tokens: tuple = ()
    max_install_days: int = 900  # installed 90 to this many days ago (an EOL box may be older)
    prompt_style: str = "debian"  # one of PROMPT_STYLES
