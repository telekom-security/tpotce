# The table of command-not-found of the Ubuntu personas

`ubuntu.txt.gz` is written by `docker/cowrie/tools/build_command_not_found.py` from the cnf indexes
of the Ubuntu archive (as data, nothing of them is run):

    http://archive.ubuntu.com/ubuntu/dists/<suite>/<component>/cnf/Commands-amd64.xz

saved as `ub-<suite>-<component>-cnf-amd64.xz` for the suites and components of the personas'
sources (noble, noble-updates, noble-security: main restricted universe multiverse; jammy main
restricted universe, jammy-updates main restricted, jammy-security main restricted universe),
downloaded 2026-10-14. Then:

    python3 -I docker/cowrie/tools/build_command_not_found.py DOWNLOADS

Checked against command-not-found 23.04.0 (ubuntu:24.04) and 22.04.0 (ubuntu:22.04) with the same
indexes in the apt lists: the same answers for every command of the table; providers of one
priority can come in another order (that is the order of the directory on the real system too).
