# Ciscoasa v2 integration

The regular Dockerfile fetches the published `v2.0.1` release from `telekom-security/ciscoasa_honeypot` and verifies its source commit (`941fb0938c91e950f667bfc3a2b2f4afcfa89596`). Release: https://github.com/telekom-security/ciscoasa_honeypot/releases/tag/v2.0.1. The local `source` context can be used for development builds.

For local development, use the named `source` build context pointing at the fork checkout, for example:

```sh
docker buildx build --load --build-context source=./ciscoasa_honeypot \
  -t ciscoasa-v2:local -f tpotce/docker/ciscoasa/Dockerfile tpotce/docker/ciscoasa
```

Run from the GitHub parent directory, then `docker/_tests/tests/ciscoasa.sh --image ciscoasa-v2:local` in the T-Pot worktree. Compose can use the same context through a temporary override with `services.ciscoasa.build.additional_contexts.source` set to the absolute fork path.

Runtime mounts are `ciscoasa/log`, `ciscoasa/identity` and `ciscoasa/payloads`, initialized by tpotinit for UID 2000. Identity contains the persistent TLS certificate and private key. IKE is published on 500/udp and 4500/udp; HTTPS stays on 8443/tcp. Customizer rebuilds warn and discard former 5000/udp overrides, including disabled publication overrides.

The ewsposter compatibility change is upstream PR https://github.com/telekom-security/ewsposter/pull/23. There is no local parser patch and no log alias; the currently pinned ewsposter cannot consume v2 `payload` fields without that fix. Complete EWS export acceptance therefore requires a later upstream merge and approved pin update.

nmap AnyConnect extracts the exact profile version; nmap IKE reports Cisco VPN Concentrator for the supplied Cisco vendor-ID signature.

The active JSONL log is limited to 64 MiB and resumes after rotation. Data files use mode 660 and directories 770. HTTP admits up to 64 concurrent requests, four per source IP, and reads each body before taking a worker. IKE answers each request at most once; repeated requests use cached responses.

Upload storage retains at most 4096 files and uses a separate 256 MiB calculated budget: content rounded up to 16 KiB plus 16 KiB per file. Existing regular files are included after restart. Existing evidence is retained when a limit is exceeded, and duplicate content remains available. This application budget is not a filesystem quota.
