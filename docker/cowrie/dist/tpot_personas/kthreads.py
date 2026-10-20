"""The kernel threads of a persona: ps lists them ([kthreadd], [kworker/0:1-events] ...), top counts
them in its Tasks (the patched ps reads [shell] kernel_thread_list, top [shell] kernel_threads), and the
pid_max of the system that sets the width of procps' PID column.

The names follow the kernel of the release (workqueue rescuers as kworker/R-<name> from Linux 6.9,
names cut at 15 characters before 5.17, the idle state I from 4.14, rcu_preempt for a PREEMPT kernel),
the per-CPU threads its CPUs and the threads of its root file system (jbd2 for ext4, xfs-* for XFS).
The list is plausible, not recorded on each system: the recorded format is that of the Docker VM
kernel (refs/procs.json kthreads:*), the sets per release are unverified."""
import re

from .common import is_login_shell, ps_start, rng_for
from .identity import drawn_at

KERNEL_THREADS = "kernel_threads.json"
# systemd sets kernel.pid_max to 4194304 from version 243 (50-pid-max.conf); older systems keep the
# kernel's 32768 (unverified: Synology DSM 7 and the firmware of the devices)
PID_MAX_SYSTEMD = 4194304
PID_MAX_KERNEL = 32768
PID_MAX_FAMILIES = frozenset({"ubuntu", "debian", "raspios", "rhel"})


def pid_max(persona):
    return PID_MAX_SYSTEMD if persona.family in PID_MAX_FAMILIES else PID_MAX_KERNEL


def kernel_version(ident):
    numbers = re.findall(r"\d+", ident.release.kernel_version)
    return tuple(int(x) for x in (numbers + ["0", "0", "0"])[:3])


def _root_device(persona, ident, filesystems):
    root = next((row for row in filesystems if row.get("target") == "/"), None)
    if root is None:
        return "", ""
    source = str(root.get("source") or "")
    name = source.rsplit("/", 1)[-1] if source.startswith("/dev/") else ""
    if name == "root":
        name = "nvme0n1p1" if ident.hardware.provider == "aws" else "sda1"
    return name, str(root.get("fstype") or "")


def _server_names(persona, ident, filesystems):
    """(name, state) of the kernel threads of a server, in the order the kernel starts them."""
    v = kernel_version(ident)
    cpus = max(1, ident.hardware.cpus)
    idle = "I" if v >= (4, 14) else "S"

    def rescuer(name):
        return (f"kworker/R-{name}" if v >= (6, 9) else name, f"{idle}<")

    def cut(name):
        return name if v >= (5, 17) else name[:15]

    preempt = "PREEMPT" in ident.release.kernel_build
    ubuntu = persona.family == "ubuntu"
    highpri = "kworker/{n}:0H-events_highpri" if v >= (5, 4) else "kworker/{n}:0H" if v >= (4, 0) else "kworker/{n}:0H"
    names = [("kthreadd", "S")]
    if v >= (6, 6):
        names.append((cut("pool_workqueue_release"), "S"))
    if v >= (5, 0):
        names.append(rescuer("rcu_gp"))
        if v < (6, 6):
            names.append(rescuer("rcu_par_gp"))
    if v >= (5, 16):
        names.append(rescuer("slub_flushwq"))
    names.append(rescuer("netns") if v >= (4, 0) else ("netns", "S<"))
    names.append((highpri.format(n=0), f"{idle}<"))
    if v >= (4, 14):
        names.append(rescuer("mm_percpu_wq"))
    if v >= (5, 8):
        names += [(cut("rcu_tasks_kthread"), idle), (cut("rcu_tasks_rude_kthread"), idle),
                  (cut("rcu_tasks_trace_kthread"), idle)]
    if v < (4, 19):
        names.append(("rcu_bh", "S"))
    names += [("ksoftirqd/0", "S"), ("rcu_preempt" if preempt else "rcu_sched", idle), ("migration/0", "S")]
    if v < (4, 14):
        names.append(("lru-add-drain", "S<"))
        names.append(("watchdog/0", "S"))
    if ubuntu and v >= (5, 4):
        names.append(("idle_inject/0", "S"))
    if v >= (4, 10):
        names.append(("cpuhp/0", "S"))
    for n in range(1, cpus):
        if v >= (4, 10):
            names.append((f"cpuhp/{n}", "S"))
        if ubuntu and v >= (5, 4):
            names.append((f"idle_inject/{n}", "S"))
        if v < (4, 14):
            names.append((f"watchdog/{n}", "S"))
        names += [(f"migration/{n}", "S"), (f"ksoftirqd/{n}", "S"), (highpri.format(n=n), f"{idle}<")]
    names += [("kdevtmpfs", "S"), rescuer("inet_frag_wq") if v >= (5, 10) else ("khelper", "S<"),
              ("kauditd", "S"), ("khungtaskd", "S"), ("oom_reaper", "S") if v >= (4, 6) else ("bdi-default", "S<"),
              rescuer("writeback"), ("kcompactd0", "S") if v >= (4, 6) else ("crypto", "S<"), ("ksmd", "SN"),
              ("khugepaged", "SN"), rescuer("kintegrityd"), rescuer("kblockd")]
    if v >= (5, 0):
        names += [rescuer("blkcg_punt_bio"), rescuer("tpm_dev_wq")]
    else:
        names += [("bioset", "S<"), ("kthrotld", "S<")]
    names += [rescuer("ata_sff"), rescuer("md"), rescuer("edac-poller"), rescuer("devfreq_wq"), ("watchdogd", "S")]
    names.append((f"kworker/u{2 * cpus}:0-events_unbound" if v >= (4, 18) else f"kworker/u{2 * cpus}:0", idle))
    names.append(("kswapd0", "S"))
    if ubuntu:
        names.append((cut("ecryptfs-kthread"), "S"))
    if v >= (5, 0):
        names += [rescuer("kthrotld"), rescuer("acpi_thermal_pm")]
    if ident.hardware.provider != "pi":
        names += [("scsi_eh_0", "S"), rescuer("scsi_tmf_0")]
    else:
        names += [rescuer("mmc_complete"), ("irq/53-mmc1", "S")]
    if v >= (5, 10):
        names.append(rescuer("mld"))
    names += [rescuer("ipv6_addrconf"), rescuer("kstrp") if v >= (4, 9) else ("deferwq", "S<")]
    if ubuntu and v >= (5, 15):
        names.append(rescuer("charger_manager"))
    if ident.hardware.gpus:
        names += [rescuer("nvidia-modeset/kthread_q"), ("nv_queue", "S"), ("nvidia", "S")]
    device, fstype = _root_device(persona, ident, filesystems)
    if device and fstype == "ext4":
        names += [(f"jbd2/{device}-8", "S"), rescuer(cut("ext4-rsv-conversion"))]
    elif device and fstype == "xfs":
        names += [rescuer("xfsalloc"), rescuer("xfs_mru_cache")]
        names += [rescuer(f"xfs-{part}/{device}") for part in ("buf", "conv", "cil", "reclaim", "log")]
        if v >= (5, 10):
            names.append(rescuer(f"xfs-gc/{device}"))
        names.append((f"xfsaild/{device}", "S"))
    return names


def _device_names(persona, ident):
    """(name, state) of the kernel threads of an embedded device (BusyBox ps shows them)."""
    v = kernel_version(ident)
    cpus = max(1, ident.hardware.cpus)
    idle = "I" if v >= (4, 14) else "S"
    if v < (2, 6, 36):
        names = [("kthreadd", "S"), ("ksoftirqd/0", "S"), ("events/0", "S"), ("khelper", "S"), ("async/mgr", "S"),
                 ("sync_supers", "S"), ("bdi-default", "S"), ("kblockd/0", "S"), ("pdflush", "S"), ("pdflush", "S"),
                 ("kswapd0", "S"), ("aio/0", "S"), ("mtdblock0", "S"), ("mtdblock1", "S"), ("mtdblock2", "S"),
                 ("mtdblock3", "S")]
        return names
    names = [("kthreadd", "S"), ("ksoftirqd/0", "S"), ("kworker/0:0", idle)]
    if v >= (3, 10):
        names.append(("kworker/0:0H", f"{idle}<"))
    if v >= (3, 0):
        names.append(("rcu_sched" if v >= (3, 10) else "khelper", idle if v >= (3, 10) else "S"))
    for n in range(1, cpus):
        if v >= (4, 10):
            names.append((f"cpuhp/{n}", "S"))
        names += [(f"migration/{n}", "S"), (f"ksoftirqd/{n}", "S"), (f"kworker/{n}:0", idle),
                  (f"kworker/{n}:0H", f"{idle}<")]
    names += [("netns", "S<" if v < (4, 14) else "I<"), ("writeback", "S<" if v < (4, 14) else "I<"),
              ("crypto", "S<" if v < (4, 14) else "I<"), ("kblockd", "S<" if v < (4, 14) else "I<"),
              ("kswapd0", "S"), ("kworker/u2:0" if cpus == 1 else f"kworker/u{2 * cpus}:0", idle)]
    if v < (4, 0):
        names += [("bdi-default", "S<"), ("fsnotify_mark", "S")]
    if persona.family in ("openwrt", "dlink", "tplink", "ubiquiti"):
        names += [(f"mtdblock{n}", "S") for n in range(5)]
    else:
        names += [("scsi_eh_0", "S"), ("scsi_tmf_0", "S<" if v < (4, 14) else "I<")]
    names.append(("deferwq", "S<") if v < (4, 14) else ("ipv6_addrconf", "I<"))
    return names


def kernel_threads(persona, ident, processes, filesystems=()):
    """The kernel threads as ps entries (PID, PPID, COMMAND in brackets, STAT, START): PIDs from 2 on,
    in the gaps of the persona's processes and never in the room below the login shell that the
    session's processes take; a few kworkers of later on among the daemons."""
    names = _server_names(persona, ident, filesystems) if persona.kind == "server" else _device_names(persona, ident)
    rng = rng_for(ident, "kernel threads")
    taken = {p["PID"] for p in processes if isinstance(p.get("PID"), int)}
    shells = [p["PID"] for p in processes if is_login_shell(p)]
    ceiling = (min(shells) if shells else max(taken | {2000})) - 40
    boot = ps_start(ident.booted_at, ident.now)
    out = []
    pid = 1

    def free(start):
        while start in taken or start in {e["PID"] for e in out[-3:]}:
            start += 1
        return start

    for index, (name, state) in enumerate(names):
        pid = free(pid + 1 + (1 if index > 2 and rng.random() < 0.15 else 0))
        out.append({"PID": pid, "PPID": 0 if name == "kthreadd" else 2, "COMMAND": f"[{name}]", "STAT": state,
                    "START": boot})
    # kworkers of later on: between the daemons, below the session
    v = kernel_version(ident)
    idle = "I" if v >= (4, 14) else "S"
    used = {e["PID"] for e in out} | taken
    queues = ("events", "mm_percpu_wq", "cgroup_destroy", "events_power_efficient") if v >= (4, 18) else ("",)
    low = max(used) + 1 if max(used) + 1 < ceiling else pid + 1
    for n in range(min(6, 2 + max(1, ident.hardware.cpus)) if v >= (2, 6, 36) else 0):
        if low >= ceiling:
            break
        candidate = rng.randint(low, max(low, ceiling))
        while candidate in used:
            candidate += 1
        if candidate >= ceiling:
            break
        cpu = n % max(1, ident.hardware.cpus)
        queue = queues[n % len(queues)]
        name = f"kworker/{cpu}:{1 + n // max(1, ident.hardware.cpus)}" + (f"-{queue}" if queue else "")
        if persona.kind != "server":
            name = name.split("-", 1)[0]
        started = rng.randint(int(ident.booted_at), int(max(ident.booted_at, drawn_at(ident))))
        out.append({"PID": candidate, "PPID": 2, "COMMAND": f"[{name}]", "STAT": idle,
                    "START": ps_start(started, ident.now)})
        used.add(candidate)
    out.sort(key=lambda e: e["PID"])
    return out
