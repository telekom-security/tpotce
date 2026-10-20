"""Healthcheck of dionaea: the CPU use of its main thread, the event loop.

Usage: cpu_check.py <PID> <CPU_USAGE_THRESHOLD>

The check looks at the event loop: it is unhealthy when the loop takes 100 % of
a core. Only the main thread (/proc/<pid>/task/<pid>/stat) is measured: the emu
scans and emulates shellcode in up to four worker threads, so a container that
is busy and works reads about 100 % as a whole at cpus 1; those threads do not
count. Average over 3 s; exit 1 at or above the threshold (percent of one core).
"""

import os
import sys
import time

SECONDS = 3


def thread_ticks(pid, proc="/proc"):
    """utime + stime of the main thread of pid, in clock ticks."""
    with open(os.path.join(proc, str(pid), "task", str(pid), "stat")) as f:
        stat = f.read()
    # the name (field 2) is in parentheses and may contain any of them
    fields = stat[stat.rindex(")") + 2:].split()
    # fields[0] is field 3 (state): utime is field 14, stime field 15
    return int(fields[11]) + int(fields[12])


def main(argv, proc="/proc", hz=None, clock=time.monotonic, sleep=time.sleep):
    if len(argv) != 3:
        print("Usage: cpu_check.py <PID> <CPU_USAGE_THRESHOLD>")
        return 1
    try:
        pid = int(argv[1])
    except ValueError:
        print("Please provide a valid integer value for the PID.")
        return 1
    try:
        cpu_threshold = float(argv[2])
    except ValueError:
        print("Please provide a valid number for the CPU usage threshold.")
        return 1
    if hz is None:
        hz = os.sysconf("SC_CLK_TCK")

    try:
        before = thread_ticks(pid, proc)
        start = clock()
        sleep(SECONDS)
        after = thread_ticks(pid, proc)
        elapsed = clock() - start
    except (OSError, ValueError, IndexError):
        print(f"No process with the PID {pid} was found.")
        return 1

    usage = 100.0 * (after - before) / hz / elapsed if elapsed > 0 else 0.0
    print(f"Average CPU usage of the main thread of PID {pid} over {SECONDS} seconds: {usage:.1f}%")
    if usage >= cpu_threshold:
        print(f"The main thread of PID {pid} is at or above the threshold of {cpu_threshold}%.")
        return 1
    print(f"The main thread of PID {pid} is below the threshold of {cpu_threshold}%. Exiting with code 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
