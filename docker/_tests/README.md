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
./docker/_tests/run.sh ddospot
./docker/_tests/run.sh dionaea
./docker/_tests/run.sh dicompot
./docker/_tests/run.sh elasticpot
./docker/_tests/run.sh endlessh
./docker/_tests/run.sh fatt
./docker/_tests/run.sh rdphoneypot
```

Common options:

```bash
./docker/_tests/run.sh --timeout 45 --bind-ip 127.0.0.1
./docker/_tests/run.sh --keep-artifacts adbhoney
```

The runner checks common and test-specific dependencies before starting
containers, and fails early with a list of missing tools.

Individual tests can also be run directly:

```bash
./docker/_tests/tests/adbhoney.sh
./docker/_tests/tests/adbhoney.sh --image dtagdevsec/adbhoney:24.04 --host-port 15555
./docker/_tests/tests/ciscoasa.sh --https-port 18443 --ike-port 15000
./docker/_tests/tests/citrixhoneypot.sh --https-port 1443
./docker/_tests/tests/conpot.sh --guardian-ast-port 11001 --ipmi-port 1623
./docker/_tests/tests/cowrie.sh
./docker/_tests/tests/cowrie.sh --ssh-port 2222 --telnet-port 2323
./docker/_tests/tests/cowrie.sh --persona debian-bookworm-vuln
./docker/_tests/tests/cowrie.sh --persona openwrt-1806
./docker/_tests/tests/ddospot.sh
./docker/_tests/tests/ddospot.sh --dns-port 1053 --ntp-port 1123 --ssdp-port 19000
./docker/_tests/tests/dionaea.sh
./docker/_tests/tests/dionaea.sh --ftp-port 2121
./docker/_tests/tests/dicompot.sh
./docker/_tests/tests/dicompot.sh --host-port 11112
./docker/_tests/tests/elasticpot.sh
./docker/_tests/tests/elasticpot.sh --http-port 19200
./docker/_tests/tests/endlessh.sh
./docker/_tests/tests/endlessh.sh --ssh-port 2222
./docker/_tests/tests/fatt.sh
./docker/_tests/tests/rdphoneypot.sh
./docker/_tests/tests/rdphoneypot.sh --rdp-port 13389
```

The Dionaea test maps the tested service ports to temporary loopback ports,
mounts a temporary config without `services/sip.yaml`, and skips the SIP ports
`5060/tcp`, `5060/udp`, and `5061/tcp`. The `--ftp-port` option pins only the
host-side FTP control port; every other tested Dionaea port remains dynamically
assigned.

The Dicompot test additionally requires DCMTK client tools on the host:
`echoscu`, `getscu`, `dcmdump`, and either `setscu` or `storescu`.

The RDPHoneypot test also verifies that `server.pem` is written to the
persistent cert volume and remains unchanged after a container restart.

## Conventions

- Put one executable test script per honeypot in `tests/<service>.sh`.
- Source `lib/common.sh` for Docker, Compose, cleanup, and artifact helpers.
- Use temporary directories under `/tmp` for logs and downloads.
- Bind host ports to loopback by default and prefer dynamic host ports.
- Fail with a clear image build hint when the target image is missing.
