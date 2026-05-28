# Docker Smoke Tests

This directory contains post-build smoke tests for T-Pot Docker images.

The tests expect images to exist locally. They do not build images, and they do
not touch production `data/` or `data_backup/` paths.

## Usage

```bash
./docker/_tests/run.sh --list
./docker/_tests/run.sh
./docker/_tests/run.sh adbhoney
./docker/_tests/run.sh ciscoasa
./docker/_tests/run.sh citrixhoneypot
./docker/_tests/run.sh conpot
./docker/_tests/run.sh cowrie
```

Common options:

```bash
./docker/_tests/run.sh --timeout 45 --bind-ip 127.0.0.1
./docker/_tests/run.sh --keep-artifacts adbhoney
```

Individual tests can also be run directly:

```bash
./docker/_tests/tests/adbhoney.sh
./docker/_tests/tests/adbhoney.sh --image dtagdevsec/adbhoney:24.04 --host-port 15555
./docker/_tests/tests/ciscoasa.sh --https-port 18443 --ike-port 15000
./docker/_tests/tests/citrixhoneypot.sh --https-port 1443
./docker/_tests/tests/conpot.sh --guardian-ast-port 11001 --ipmi-port 1623
./docker/_tests/tests/cowrie.sh
./docker/_tests/tests/cowrie.sh --ssh-port 2222 --telnet-port 2323
```

## Conventions

- Put one executable test script per honeypot in `tests/<service>.sh`.
- Source `lib/common.sh` for Docker, Compose, cleanup, and artifact helpers.
- Use temporary directories under `/tmp` for logs and downloads.
- Bind host ports to loopback by default and prefer dynamic host ports.
- Fail with a clear image build hint when the target image is missing.
