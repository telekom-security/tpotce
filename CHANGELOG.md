<!-- Draft for T-Pot 24.04.2 - review and complete before the release -->
# Release Notes / Changelog
T-Pot 24.04.2 moves the Elastic Stack to 9.5 on the official Elastic images, makes updating a lot safer with backups you can actually restore, and adds new honeypots and NSM tooling.

## New Features
* **T-Pot Manager** (`tpot`) shows, configures, updates and restores T-Pot from one menu and as sub-commands, see the README section *The T-Pot Manager (tpot)*. Over SSH it uses a palette made for 256 colours unless the terminal says it can show true colours, or the 16 ANSI colours where the terminal says only those: iTerm2, kitty, Ghostty, VS Code, VTE terminals, Konsole and Windows Terminal are recognised without `COLORTERM` (the same rule as the T-Pot scripts), T-Pot accepts `COLORTERM` and `LC_TERMINAL` from SSH now (`/etc/ssh/sshd_config.d/tpot.conf`), send `COLORTERM` with `SendEnv COLORTERM`, or choose the colours with `TPOT_COLORS`. Ctrl+C at a question of `tpot` (and at the sudo prompt of `tpot start|stop|restart`) ends it with exit code 130, during a command of the menu it comes back to the menu, an interrupted `tpot sensors add` takes the access back, an interrupted edition switch goes back or finishes; where `tpot` cannot ask (no terminal) the exit code is 2. The HIVE address of a sensor is an IPv4 address or a name with an IPv4 (A) record, an IPv6-only HIVE cannot take sensors yet (the SENSOR itself may be IPv6), user names may have capitals.
* **RDPHoneypot** a Remote Desktop honeypot for RDP connection and credential telemetry.
* **The T-Pot scripts** (install, update, restore, uninstall, genuser, deploy) start with the T-Pot logo at a terminal, ask and show their progress through gum with spinners, end with a summary and share one style of `-h` (now exit 0, without the logo). update.sh, restore.sh and uninstall.sh stop outside Linux with a hint, `-h` works everywhere; `install.sh` runs with the bash 3.2 of macOS up to its check of the distribution; `genuser.sh` and `deploy.sh` check the options of the `tpot` command they hand over to before they show anything; `update.sh` brings the sshd drop-in of an earlier T-Pot along (`AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION`). The version they show comes from the file `version`; `python3 -m tpotctl.release set-version X` sets a new one everywhere it is needed.
* **Image Builder** (`docker/_builder/builder.sh`) builds unattended with options (images or groups, platforms, push targets, version and repositories, smoke tests afterwards), has a menu at a terminal, settings of your own in `docker/_builder/.env.local` (`--set`, `--unset`, `--show-config`) and the builder setup (`--setup`, `--uninstall`). It runs on Linux for any user Docker answers to (rootless Docker too), root only for an upload limit while pushing; a build option next to `--set`, `--check` and the like is a usage error; a failed write of the settings keeps the old file; a push for one platform needs a tag of its own that is no plain version (i.e. `24.04.2-arm64`); an `.env.local` that is no text stops it; the menu preselects the defaults and answers *No* by default.
* **Restore Script** `restore.sh` puts a backup written by `update.sh` back, as a whole or per group (checkout, configuration, `data/`, Kibana objects and ILM policy).
* **Update Script** has been reworked:
  * Backups go to `~/tpot_backups`, rotate, are checked for space and hold what git cannot bring back; `--full` adds all of `data/`.
  * Your Kibana objects and the ILM policy are exported before T-Pot is stopped.
  * The installed edition is detected and restored, `-b <branch>` / `-r <repo>` allow testing branches and forks, `-s` starts T-Pot after the update.
  * Before pulling a new Elastic Stack the update checks the disk space and warns if the backup does not hold the Elasticsearch data.
* **Cowrie Personas** let Cowrie present itself as different systems.
* **Conpot** emulates five further ICS devices: a Beckhoff CX embedded PC (ADS, ADS discovery, OPC UA), a LOYTEC building automation server (KNXnet/IP, BACnet/IP), a WirelessHART gateway (HART-IP), an ICCP / TASE.2 endpoint and a Schneider Modicon M340 (Modbus/TCP), the IEC 104 RTU now also speaks DNP3. Serial numbers, MAC and IP addresses, host names and similar identifiers are generated once per installation (`data/conpot/identity`) instead of shipping the same values on every T-Pot.
* **Landing Page** of the web UI has been rebuilt. Next to the tools, laid out as cells of a honeycomb, it shows the attacks of the last 24 hours, the last hour or the last minute live from Elasticsearch: the trend against the range before, the latest attack, the sources, the honeypots hit, a graph per honeypot, the top sources and, on a HIVE, its sensors. Three background effects follow the attacks (Comb, Honey, Network), `?kiosk` or the key `f` turn it into a wall display. It loads nothing from elsewhere, runs no inline code under a strict Content-Security-Policy, checks its files by their hash and shows values that come from attackers as text only; every error, the login prompt too, gets a neutral page that names neither T-Pot nor nginx. particles.js and Font Awesome are gone.
* **Smoke Tests** for the honeypot images (`docker/_tests`) and an end-to-end test of the Attack Map pipeline (`attackmap_pipeline_test.sh`).
* **Listbot** translation maps are cached.
* **P0f** adds `os_family` (Android, Apple, Linux, Windows, BSD, Other or Scanner), `os_confidence` and `os_samples` to client SYNs; `os_confidence` and `os_samples` only come with fingerprints that have enough data in the public datasets, the others (i.e. BSDs, old Linux and Windows versions) get the family alone. The confidence is the share of that family among the flows p0f gives the same label in two public, labelled datasets (CESNET 2024, MUNI 2021, CC BY 4.0) and comes from the new `conf` field of `p0f.fp` (`docker/p0f/tools/build_os_confidence.py`). Scanners get no confidence, fuzzy matches only the family.
* **P0f** flags clients whose User-Agent does not fit their TCP stack: `http request` events carry `user_agent`, `ua_os` (the OS family the User-Agent names), `os_family` / `os_confidence` of the connection's SYN, `ua_os_mismatch` (i.e. a Windows User-Agent from a Linux stack; Linux and Android count as one), `ua_dishonest` (the User-Agent does not fit the HTTP header order) and `http_proxy` (Via or X-Forwarded-For: the TCP stack may be the proxy's). The new Kibana dashboard **P0f** shows these signals with the OS families and scanners, >T-Pot shows the signal counts; the dashboards leave out mismatches against TCP fingerprints the datasets measured below an `os_confidence` of 0.9 and requests through a proxy (also from the header order signal). P0f only sees plain-text HTTP and the first request of a connection; values that are no valid UTF-8 are logged with their bytes from 0x80 up as `\xNN`.
* **P0f** reads pcap files offline and prints JSON: `docker run --rm -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/capture.pcap`, a BPF filter can follow, own `-j` / `-o` replace the defaults. On success it prints the JSON lines only, warnings and errors go to stderr. The container runs as uid 2000, pcaps only you can read need `--user "$(id -u):$(id -g)"`.

## Updates
* **Elastic Stack** has been updated to 9.5.4 (from 8.16.1) and is now built on the official Elastic images.
* **Elasticsearch** no longer rejects events once a daily index reaches its field limit (now 3000), further new fields are not indexed then but stay in the document.
* **Kibana** now runs with a 1 GB Node.js heap (`KIBANA_HEAP_MB` in `docker/elk/kibana/Dockerfile`) within a `mem_limit` of 2 GB.
* **Attack Map** has been updated to 4.0.0.
* **Beelzebub** has been updated to 3.9.2 and is built from upstream again instead of the T-Pot fork. The log format, the dashboards and the SSH host key stay as they were, further services (Telnet, MCP, LDAP, SMB, MSSQL, MQTT, RDP, PostgreSQL, VNC, Redis, Memcached) are prepared; the LLM edition publishes all of them that Galah does not use (22, 23, 389, 445, 1433, 1883, 2222, 3306, 3389, 5432, 5900, 6379, 8000, 8081, 8888, 11211); added to another edition with the customizer, it publishes only 22. Beelzebub and Galah now default to the model `llama3.1:8b` (see the README section *Ollama* for smaller and larger ones).
* **Conpot** has been updated to 1.0.0, pinned to a later commit of the master branch (asyncio, TOML templates, new event schema). All Conpot and upstream default strings of the deployed templates have been replaced. The pinned commit includes fixes contributed upstream: Guardian AST no longer keeps a core at 100% CPU after a client sent anything but a command and closed the connection (the reason for the former CPU health check, which has been removed), IPMI answers again to clients that come back from a new source port, and Kamstrup and ICCP no longer crash on binary or malformed input.
* **Cowrie** has been updated to 3.0.0, pinned to a later commit of the main branch.
* **Suricata** has been updated to 8.0.7 (Alpine 3.24 package). The rules are now cached in `data/suricata/rules` and updated once in 24 hours (`SURICATA_RULES_UPDATE=off` never downloads them), a failed update or a start without internet access uses the latest cached rules, and the capture filter keeps excluding the T-Pot ports without DNS.
* **P0f** fingerprints have been updated for Windows 10 and newer, Linux 4.19 and newer, Android, current macOS / iOS, IPv6 and the scanners masscan, zmap and SYNs without TCP options (Mirai, hping). In a replay of the public datasets the right OS family rises from 76 % to 94 % on the data the new signatures come from, on an independent dataset (MUNI 2019) it stays at 97 %. SYNs the T-Pot host sends itself are no longer logged (by packet direction, so new or changed addresses are covered; connections the host opens are left out completely, the SYN+ACK and HTTP response of those servers too), and VLAN tagged traffic (802.1Q, QinQ, i.e. on mirror ports) is fingerprinted again; so far p0f silently dropped every tagged packet. The Kibana visualization `P0f OS Distribution` shows `os_family` of client SYNs with `os_confidence` of 0.9 or more, plus scanners; events of earlier releases have no `os_family`.
* **NSM services** (Suricata, P0f, Glutton) capture on the interface of the route to the internet, so hosts with more than one default route no longer end up in a restart loop. `TPOT_CAPTURE_INTERFACE` in `.env` sets the interface manually.
* **Update Script** adds settings that are new in `env.example` to an existing `.env` and comments out those that are no longer used.
* **T-Pot Init** validates all settings of `.env` and reports every invalid one at once before T-Pot starts. Settings of a service are only checked if it runs in the active edition, `TPOT_CAPTURE_INTERFACE` and the Attack Map time zone are checked against the host.
* **Nginx** has been updated to 1.28.3 (Alpine 3.23 package).
* **Cyberchef** has been updated to 11.0.0.
* **Galah** has been updated to 1.1.1 and is built from upstream again instead of the T-Pot fork. The log format stays as it was, failed LLM responses now show up in the Galah dashboard, and all LLM settings (API key, temperature, GCP Vertex AI) can be set in `.env`.
* **Elasticvue** has been updated to 1.15.0.
* **EWSPoster** has been updated to 1.33, pinned to a commit of the master branch, and no longer stops on an empty honeypot log file (Python 3.13 and later).
* **Go-Pot** has been updated to 1.2.0-rc-7.
* **H0neytr4p** has been updated to 0.44.
* **Hellpot** has been updated to 0.60.
* **Heralding** has been updated to 2.0.0 (pre-release, master branch of the T-Pot fork) on Python 3.14 and catches credentials on 27 instead of 16 services. New services only get a host port where no other honeypot of the edition uses it: 389, 636, 990 and 8883 in Standard, Sensor and Mobile, 445 as well in Mac / Win, 445, 587, 1433, 1883, 6379 and 5060 (TCP / UDP) as well in Tarpit. RDP logs plaintext logins of the TLS fallback and NTLM hashes of NLA. `auth.csv` and `log_session.json` keep their format.
* **Honeyaml** builds again: its Rust dependencies have been updated and it is now a static binary on a scratch image (branch `tpot-24.04.2` of the T-Pot fork, upstream is no longer maintained).
* **IPPHoney** has been updated to 2.0.2.
* **Mailoney** has been updated to 3.0.0 (T-Pot fork, pinned to the release): SMTP on 25 and Submission on 587 (both with STARTTLS), SMTPS on 465 (implicit TLS), logins with AUTH PLAIN / LOGIN, mails and their attachments are stored (`data/mailoney/log/mails`, archived daily to `data/mailoney/mails.tgz` like the downloads of other honeypots) and floods are limited per source and sender. The server name of its banner and TLS certificate is chosen once per installation (`data/mailoney/log/identity`). Mailoney takes port 465, Heralding no longer listens there in the Standard, Sensor, Mobile and Mac / Win editions. The new Mailoney dashboard shows mails, attachments, logins, TLS sessions and SMTP input, the >T-Pot Username / Password tag clouds include its logins, see [Breaking Changes](#mailoney).
* **Miniprint** has been updated to 0.2.2 (T-Pot fork, pinned to the release): Brother, HP and Lexmark printer personas with one identity per installation (`data/miniprint/data`), a web admin interface on port 8000 (the Brother serial number leak, default password login, LDAP / SMTP passback and firmware upload, with CVE hints), print jobs captured as PostScript, PCL, PDF or raw files and limits per connection. The Miniprint dashboard shows the lure chain, CVE hints, PJL commands, print languages, uploads, passback targets, URLs and User-Agents, see [Breaking Changes](#miniprint).
* **RedisHoneyPot** has been updated to 2.0.2 (T-Pot fork, pinned to the release): it speaks RESP2 and RESP3 and answers as a real Redis or Valkey server, with replies, `INFO`, `CONFIG GET *` and the command table recorded from the real servers; identifying values change with every start. `REDISHONEYPOT_PROFILE` in `.env` picks the persona: `redis74` (Redis 7.4.5, the default), `legacy6` (6.2.18), `current8` (8.8.0), `redis50` (5.0.7) or `valkey8` (Valkey 8.1.3). File write playbooks (`CONFIG SET dir` / `dbfilename` and `SAVE`), `SLAVEOF`, `MODULE LOAD` and Lua scripts are answered and logged, but nothing is executed, written or connected to. The container has a health check now. The new Redishoneypot dashboard shows commands, analysis hints, file write targets, replica hosts, module paths, scripts, IOCs (URLs, domains, IPs) and client libraries, the >T-Pot Username tag cloud includes its `AUTH` users, see [Breaking Changes](#redishoneypot).
* **Wordpot** has been updated to 3.0.1 (T-Pot fork, pinned to the release) and runs with Gunicorn: one of four WordPress sites (WordPress 7.0 or 6.9.2 with their themes and plugins) per start, the next one after every restart, REST API, XML-RPC (`system.multicall`, pingbacks), plugin and theme probes, config file and web shell baits and uploads are answered and logged, but nothing is executed, fetched or served. Request bodies are stored by their SHA-256 (`data/wordpot/log/payloads`, archived daily to `data/wordpot/payloads.tgz`). The container has a health check now. The Wordpot dashboard shows techniques, request paths, probed plugins and themes, the site profile and payloads, the >T-Pot Username / Password tag clouds include its XML-RPC logins, see [Breaking Changes](#wordpot).
* **Honeypots** without releases were updated to their latest pushed code, pinned to a commit.
* Docker images now use **Alpine 3.24**, **Go 1.26** or **Scratch** wherever possible; **Log4Pot** moved from Ubuntu to Alpine.
* **Installer** supports unattended installations and has been tested with **Alma 10**, **Debian 13**, **Fedora 44**, **OpenSuse Tumbleweed**, **Rocky 10**, **RHEL 10** and **Ubuntu 26.04** (sudo-rs).
* **Persistence** cycles for logrotate are configurable through `TPOT_PERSISTENCE_CYCLES` in `.env`.
* **Beelzebub** and **Honeypots** log their status as text (i.e. `Stateless`, `failed`), it is now indexed as `status_text`. So far Elasticsearch could not index it in the numeric `status` field, some values ended up as `0` and with 24.04.1 part of these events were not indexed at all.
* **Kibana** >T-Pot Username / Password tag clouds now include the logins of **Honeypots** (import the Kibana objects of this release). **Heralding** no longer indexes the header row of `auth.csv` as a login with username `username` and password `password`.
* **Fatt** has been removed, see [Breaking Changes](#fatt).
* **Snare / Tanner** has been removed, **H0neytr4p** now also takes port 80, see [Breaking Changes](#snare--tanner).
* **Spiderfoot** has been removed, see [Breaking Changes](#spiderfoot).
* Updates for `24.04.2` images will be provided continuously through Docker image updates.

## Breaking Changes
### Elastic Stack
- Elasticsearch and Kibana upgrade their data in `~/tpotce/data/elk` on the first start, ***this cannot be undone***. Run the update with `update.sh -y --full` or take a snapshot of the machine first, see [Elastic Stack Upgrades](README.md#elastic-stack-upgrades).
- If you use a `docker-compose.yml` of your own, raise the `mem_limit` of the `kibana` service to `2g`.
- The official Elastic images are based on UBI 9 and require a **x86-64-v2** capable CPU on x86 hosts, otherwise Elasticsearch, Kibana and Logstash stop with `Fatal glibc error: CPU does not support x86-64-v2`. Virtual machines using the legacy `kvm64` / `qemu64` CPU models are affected, e.g. Proxmox VE VMs created before 8.0 or QEMU without `-cpu`. Use the `host` CPU type or at least `x86-64-v2`.

### Update Script
- Updates are supported from **24.04.1** onwards. Installations of 24.04.0 need a fresh install.

### Image Builder
- `docker/_builder/setup_builder.sh` is gone, use `docker/_builder/builder.sh --setup` / `--uninstall`.

### Conpot
- Conpot logs the event schema of Conpot 1.0: `protocol` replaces `data_type`, `session_id` replaces `id`, `event_time` and `session_time` replace `timestamp`, further protocol details are indexed as `conpot_data`. Import the Kibana objects of this release, the Conpot dashboard then shows the protocol from `protocol`; older events keep `data_type`.
- EWSPoster reads both formats, older EWSPoster images stop with an error on the new Conpot logs.
- If you use a `docker-compose.yml` of your own, add the volume `${TPOT_DATA_PATH}/conpot/identity:/var/lib/conpot` to the Conpot services and take the new services from `compose/standard.yml`.

### Dionaea
- The SIP service has been removed.

### Fatt
- Fatt has been removed, Suricata already logs its fingerprints (JA3 / JA3S / JA4, HASSH, RDP, HTTP, QUIC) and detects the protocols on every port, Fatt only looked at a fixed set of ports. The Dockerfile has moved to `docker/deprecated/fatt`.
- The Suricata dashboard now also shows SSH HASSH, RDP client names and HTTP URLs. Import the Kibana objects of this release to get it; the import does not delete the Fatt dashboard, remove it under Stack Management → Saved Objects (tag `Fatt`).
- `update.sh` removes the `fatt` service from a `docker-compose.yml` of your own, the previous file stays in the backup.
- The logs in `~/tpotce/data/fatt` are kept. If you no longer need them, remove the folder with `sudo rm -rf ~/tpotce/data/fatt`.

### Mailoney
- Mailoney logs one JSON event per mail, login or session to `data/mailoney/log/log.json` instead of `commands.log` / `mail.log`. In Elasticsearch `data` and `emails` are gone, the events carry `event_type` (`mail`, `auth`, `session`, `rate_limit_block`), `mail.*`, `attachment.*`, `username` / `password`, `auth.*`, `tls.*`, `listener.*`, `session_outcome`, `session_seconds` and `smtp_input` (one entry per line in `smtp_commands`). Import the Kibana objects of this release to get the new Mailoney dashboard, older events keep their fields.
- EWSPoster sends no Mailoney events until it reads the new format.
- If you use a `docker-compose.yml` of your own, publish `465:465` and `587:587` for Mailoney (instead of `587:25`) and comment out `465:465` of Heralding.

### Miniprint
- Miniprint logs one session per connection (`session_id`, `protocol` `pjl` or `http`, `persona`, `session_start` / `session_end`), virtual paths as `virtual_path` (instead of `dir`), saved jobs as `save_print_job` / `save_raw_print_job` / `save_postscript` / `save_firmware` with `file_name`, `artifact_type`, `payload_sha256` and `size`, admin form fields as `form_fields` and the server of an LDAP / SMTP passback as `passback_target`; command responses are no longer logged. In Elasticsearch the session length is `session_seconds`. Import the Kibana objects of this release to get the new Miniprint dashboard, older events keep their fields.
- EWSPoster reports only PJL connections of Miniprint until it reads the new format.
- If you use a `docker-compose.yml` of your own, publish `8000:8000` for Miniprint and mount `${TPOT_DATA_PATH}/miniprint/data/:/opt/miniprint/data/`.

### Redishoneypot
- RedisHoneyPot logs one JSON event per connection, command and close (`event`) with `src_ip` / `src_port`, `session_id`, `command`, `args_text`, `analysis_hint`, `outcome`, the persona as `profile` and fields for payloads and IOCs, instead of `action` / `addr`. In Elasticsearch the session length is `session_seconds`, the `AUTH` user is `username`, `ioc_urls`, `ioc_ips`, `ioc_domains` and `session_hints` hold one entry each. Import the Kibana objects of this release to get the new Redishoneypot dashboard, older events keep their fields.
- EWSPoster needs an update for the new format, older EWSPoster images stop with an error on the new RedisHoneyPot logs.
- If you use a `docker-compose.yml` of your own, add `REDISHONEYPOT_PROFILE=${REDISHONEYPOT_PROFILE:-redis74}` to the `environment` of the `redishoneypot` service to choose the persona in `.env`.

### Wordpot
- Wordpot logs one JSON event per request to `data/wordpot/log/wordpot.json` instead of `wordpot.log`, with `technique` (i.e. `credential_attempt`, `xmlrpc_multicall`, `plugin_probe`, `config_bait_served`, `upload_lure_payload`) instead of `plugin`, `component_type` / `component_slug`, `profile_id`, `method`, `path`, `query`, `response_status`, `payload_sha256` / `payload_size` / `payload_ref` and `details` (i.e. `details.credential_pairs` of XML-RPC, `details.uploaded_files`); `filename`, `author` and `info` are gone. Suspicious request parameters are `details.lure_params`, one `name` / `value` pair each, a single XML-RPC login also sets `username` / `password`. Import the Kibana objects of this release to get the updated Wordpot dashboard, older events keep their fields.
- EWSPoster sends no Wordpot events until it reads the new format.
- If you use a `docker-compose.yml` of your own, add `tmpfs: - /tmp:uid=2000,gid=2000` to the `wordpot` service.

### Snare / Tanner
- Snare and Tanner have been removed, together with Tanner's API, PHPox and its Redis. The Dockerfiles have moved to `docker/deprecated/tanner`.
- **H0neytr4p** now also listens on port 80 in the Standard, Sensor, Mobile and Mac / Win editions. The Redis image of the Attack Map stays, it is built from `docker/redis` now.
- Import the Kibana objects of this release: the overview dashboards no longer list Tanner. The import does not delete the Tanner dashboard, remove it under Stack Management → Saved Objects (tag `Tanner`).
- `update.sh` removes the `snare`, `tanner`, `tanner_api`, `tanner_phpox` and `tanner_redis` services from a `docker-compose.yml` of your own, the previous file stays in the backup. Port 80 for H0neytr4p is not added there, uncomment `- "80:80"` in its block yourself.
- The logs and downloads in `~/tpotce/data/tanner` are kept. If you no longer need them, remove the folder with `sudo rm -rf ~/tpotce/data/tanner`.

### Spiderfoot
- Spiderfoot has been removed. As an OSINT / reconnaissance tool it does not fit the defensive scope of T-Pot. The service, the `/spiderfoot/` route and the link on the landing page are gone, the Dockerfile has moved to `docker/deprecated/spiderfoot`.
- `update.sh` removes the `spiderfoot` service from a `docker-compose.yml` of your own, the previous file stays in the backup.
- Your scans in `~/tpotce/data/spiderfoot` are kept. If you no longer need them, remove the folder with `sudo rm -rf ~/tpotce/data/spiderfoot`.

### Galah
- In Elasticsearch the request and response fields of Galah are now `http_request.*` and `http_response.*` (i.e. `http_request.method`, `http_request.requestURI`), as ConPot and Miniprint log `request` as text and the daily index could only hold one of them. Import the Kibana objects of this release to get the updated Galah dashboard, older events keep the previous field names.

### T-Pot Config File
- `TPOT_PERSISTENCE` accepts only `on` / `off`, `TPOT_BLACKHOLE` and `TPOT_ATTACKMAP_TEXT` only `ENABLED` / `DISABLED`. Other values such as `true` passed the check before but were not acted on; with `TPOT_PERSISTENCE=true` the honeypot logs were deleted on every start. `update.sh` rewrites these values, T-Pot does not start with any other value.

### Beelzebub
- The LLM settings in `.env` are now `BEELZEBUB_LLM_PROVIDER` (`ollama` or `openai`), `BEELZEBUB_LLM_MODEL` (the model name, i.e. `llama3.1:8b` or `gpt-4o-mini`), `BEELZEBUB_LLM_HOST` and `BEELZEBUB_LLM_API_KEY`. `update.sh` migrates the previous settings (`BEELZEBUB_LLM_MODEL: "ollama"` / `"gpt4-o"`, `BEELZEBUB_OLLAMA_MODEL`), a `docker-compose.yml` of your own needs the new `environment` block of the `beelzebub` service from `compose/llm.yml`.
- In the LLM edition Beelzebub publishes further ports (see *Updates*). A compose file of your own that the customizer built on the LLM edition and that adds services on these ports (i.e. Dionaea on 445, 1433, 1883, 3306) now has a port conflict: `update.sh` keeps it unchanged and says so, change the port in the customizer (`p`) or remove the service.

## Thanks & Credits
A heartfelt thank you to the contributors who made this release possible:
* @plygrnd for adding support for Red Hat Enterprise Linux!
* @regulartim for reporting #1866!
* Kevin Setz for pointing out exposed ENVs!
* @trixam for the fix of #1807!

… and to the entire T-Pot community for opening issues, sharing ideas, and helping improve T-Pot!
