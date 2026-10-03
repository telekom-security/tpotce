# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

T-Pot is a multi-honeypot platform: ~30 honeypots plus NSM tools (Suricata, p0f) and the Elastic Stack, all run as Docker containers via docker compose. There is no application build in the usual sense — the repo is Dockerfiles, compose files, shell scripts, and an Ansible-based installer. The installed checkout lives at `~/tpotce` on the target host, and many scripts hard-code that path (`$HOME/tpotce`).

## Commands

### Building images (`docker/_builder/`)
- `docker/_builder/docker-compose.yml` is the build-only manifest listing every image (multi-arch amd64/arm64 via buildx). Its settings (`TPOT_DOCKER_REPO`, `TPOT_GHCR_REPO`, `TPOT_VERSION`, platforms) come from `docker/_builder/.env`.
- Build all: `sudo docker/_builder/builder.sh` (run from `docker/_builder`; requires root; `-n` no-cache, `-p` push to Docker Hub + GHCR). `setup_builder.sh` prepares a build host.
- Build one image: `cd docker/_builder && docker compose build <service>`, or `cd docker/<service> && docker compose build` (each service dir has its own standalone `docker-compose.yml` with `build: .`).

### Smoke tests (`docker/_tests/`)
Post-build container tests; they require the image to already exist locally and never build.
- All: `./docker/_tests/run.sh`; list: `./docker/_tests/run.sh --list`
- One: `./docker/_tests/run.sh cowrie` or directly `./docker/_tests/tests/cowrie.sh [--image ...] [--<proto>-port ...]`
- Options: `--timeout SEC`, `--bind-ip IP`, `--keep-artifacts`
- Conventions for new tests: one executable `tests/<service>.sh` per honeypot, source `lib/common.sh` (`test_*` helpers for compose, port checks, waiting on log text, cleanup), bind to loopback, prefer dynamic host ports, temp files under `/tmp`, fail with an image build hint when the image is missing. Some tests need host tools (e.g. dicompot needs DCMTK).

### On a running T-Pot host (from `docker/tpotinit/dist/bin/`)
- `hptest.sh <host>` — probes honeypots listed in `~/tpotce/docker-compose.yml`.
- `attackmap_pipeline_test.sh [--types ...] [--ips ...] [--dry-run]` — end-to-end check: injected honeypot JSON → Logstash → Elasticsearch → map_data → Redis → map_web WebSocket. Injected events are real and not cleaned up.

### Lifecycle
`install.sh`, `update.sh`, `uninstall.sh`, `restore.sh`, `genuser.sh`, `deploy.sh` (sensor → hive). Both `install.sh` and `update.sh` accept `-b <branch>` / `-r <repo-url>` (env `TPOT_BRANCH` / `TPOT_REPO_URL`) for testing a branch or fork; see README "Testing a Branch" for precedence rules. Runtime is controlled via `systemctl start|stop tpot` (unit in `installer/install/tpot.service`).

## Architecture

- **`tpotinit` is the orchestrator.** Every compose file starts with the `tpotinit` service (host networking, NET_ADMIN, docker socket, mounts `${TPOT_DATA_PATH}` as `/data`); all other services `depends_on` it being healthy. Its `docker/tpotinit/dist/entrypoint.sh` validates `.env`, creates/permissions the data folder, sets up web users, blackhole routes, iptables NFQ rules (for glutton/honeytrap), interface config for the NSM services, imports Kibana objects on fresh HIVE installs, and runs autoheal. The capture interface comes from `dist/bin/capture-if.sh` (`TPOT_CAPTURE_INTERFACE` or the route to the internet); identical copies live in `docker/{suricata,p0f,glutton}/dist/` and must be kept in sync. Host-side helper scripts ship inside this image under `dist/bin/`.
- **Editions are compose files in `compose/`.** `standard.yml`, `sensor.yml`, `mini.yml`, `llm.yml`, `tarpit.yml`, `mobile.yml`, `mac_win.yml`. The root `docker-compose.yml` is the active one and is a copy of `compose/standard.yml` — keep them in sync when editing the standard edition. `tpot_services.yml` is the service catalogue that `compose/customizer.py` (logic in `customizer_core.py`, curses/text UI in `customizer_tui.py`, PyYAML + stdlib, Python ≥ 3.9) uses to build a custom compose file: an edition as base plus added/removed services and host-port overrides, written into a `# customizer:` header with a sha256 of the body so `update.sh` (`fuRESTORE_CUSTOM`) can `--rebuild` it on updates unless it was hand-edited. Every catalogue service needs an `x-tpot` line (group, description, optional requires/conflicts/hidden/hive_only/linux_only/required); editions may only differ from the catalogue in ports, networks and depends_on (mac_win also network_mode, cap_add, volumes). Run `python3 -m unittest discover compose/tests` after touching any compose file. Adding/changing a service usually means touching several of these files plus `docker/_builder/docker-compose.yml` and `docker/<service>/docker-compose.yml`.
- **Configuration**: `.env` (defaults in `env.example`) feeds both compose variable substitution (`TPOT_REPO`, `TPOT_VERSION`, `TPOT_PULL_POLICY`, `TPOT_DATA_PATH`, ...) and tpotinit (`TPOT_TYPE=HIVE|SENSOR`, `TPOT_BLACKHOLE`, `TPOT_HIVE_*`, `WEB_USER`, ...). Note `.env` and `docker-compose.yml` are listed in `.gitignore` but are tracked — edits to them do show in diffs.
- **Per-service layout**: `docker/<service>/{Dockerfile, docker-compose.yml, dist/}`. Containers are generally `read_only` with tmpfs, run as uid/gid 2000, and write logs to `${TPOT_DATA_PATH}/<service>/log`. Image tags follow `version` (currently `24.04.2`).
- **Data flow**: honeypots write JSON/log files under `/data/<service>/log/` → Logstash (`docker/elk/logstash/dist/logstash.conf`) tails each file with a per-honeypot `type` and filters (date, geoip, ip_rep, hostname) → Elasticsearch `logstash-*` → Kibana. On a SENSOR, Logstash swaps in `pipelines_sensor.yml` and ships events over HTTPS (`http_output.conf`) through nginx to the hive's `http_input.conf`. A new honeypot needs a Logstash input + filter block to show up in Kibana/Attack Map.
- **Beelzebub and Galah** (`docker/beelzebub/`, `docker/galah/`) build their upstream repos at a pinned commit plus a T-Pot overlay: new Go files in `dist/tpot/` (the flat log format of the former T-Pot forks; Beelzebub also persists the SSH host key and expands `${VAR}` in service YAMLs) and one-line hooks in `dist/tpot.patch`. On an upstream bump, update the commit in the Dockerfile; a hook that no longer applies or compiles fails the build. The log format must stay as it is, ewsposter reads `beelzebub.json` / `galah.json` directly; ES-only normalization (`status_text`, Galah's `http_request.*` / `http_response.*` via `de_dot`) lives in Logstash. Both builders cross-compile on `$BUILDPLATFORM` (Go crashes under QEMU), Galah needs CGO and uses `tonistiigi/xx`.
- **p0f** (`docker/p0f/`) is the original p0f 3.09b C code with T-Pot changes: JSON log (jansson), per-packet skipping of up to two VLAN tags on Ethernet (the original only handled untagged frames), `-o -` for JSON on stdout (offline mode via the entrypoint `dist/run.sh`: no arguments = live capture with a BPF filter for the host's own SYNs by packet direction (`outbound`, live only), arguments go to p0f, own `-j` / `-o` replace the defaults), an optional `conf = <family>[:<share>:<samples>]` field after OS labels in `[tcp:request]` of `p0f.fp`, which p0f writes as `os_family` / `os_confidence` / `os_samples` for client SYNs, and a `ua_family` table in `[http:request]` for the User-Agent signals of `http request` events (`ua_os`, `ua_os_mismatch` against the family of the same connection's SYN, `ua_dishonest`, `http_proxy`); the Kibana objects for it come from a builder script and live in `kibana_export.ndjson`. p0f drops flows after tool (scanner) SYNs on purpose, so those never get HTTP events. The `conf` fields are generated by `docker/p0f/tools/build_os_confidence.py` from public datasets (CC BY 4.0) and must be regenerated after signature changes; scanner signatures stay at the top of `[tcp:request]` (p0f's duplicate check).
- **Attack Map** (`docker/elk/map/`): builds from the external `telekom-security/t-pot-attack-map` repo at a pinned tag; `map_data` polls ES and publishes to Redis, `map_web` serves the WebSocket/UI behind nginx.
- **nginx** (`docker/nginx/`) is the single web entry point (port 64297) proxying Kibana, CyberChef, Elasticvue, Attack Map, and the sensor ingest endpoint.
- **Installer**: `install.sh` bootstraps prerequisites and runs the Ansible playbook `installer/install/tpot.yml` (supports multiple distros); `installer/remove/` mirrors it for uninstall.

## Conventions

- Commit messages use conventional prefixes (`feat:`, `fix:`, `refactor:`).
- Shell scripts use the project's `myVAR` / `fuFUNCTION` naming style in many scripts (e.g. `update.sh`); match the style of the file being edited.
- CI (`.github/workflows/`) only runs a README link checker and issue housekeeping — there is no automated build or test in CI, so run the relevant smoke test locally.
