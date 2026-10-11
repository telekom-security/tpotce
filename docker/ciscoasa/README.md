# Ciscoasa v2 integration

The regular Dockerfile fetches the published `v2.0.0` release from the fork and verifies its source commit (`b1d8f422b72647562b432fe74c2931332332c54b`). Release: https://github.com/t3chn0m4g3/ciscoasa_honeypot/releases/tag/v2.0.0.

For local development, use the named `source` build context pointing at the fork checkout, for example:

```sh
docker buildx build --load --build-context source=../ciscoasa_honeypot \
  -t ciscoasa-v2:local -f tpotce-ciscoasa-v2/docker/ciscoasa/Dockerfile tpotce-ciscoasa-v2/docker/ciscoasa
```

Run from the GitHub parent directory, then `docker/_tests/tests/ciscoasa.sh --image ciscoasa-v2:local` in the T-Pot worktree. Compose can use the same context through a temporary override with `services.ciscoasa.build.additional_contexts.source` set to the absolute fork path.

Runtime mounts are `ciscoasa/log`, `ciscoasa/identity` and `ciscoasa/payloads`, initialized by tpotinit for UID 2000. Identity contains the persistent TLS certificate and private key. IKE is published on 500/udp and 4500/udp; HTTPS stays on 8443/tcp. Customizer rebuilds warn and discard former 5000/udp overrides, including disabled publication overrides.

The ewsposter compatibility change is upstream PR https://github.com/telekom-security/ewsposter/pull/23. There is no local parser patch and no log alias; the currently pinned ewsposter cannot consume v2 `payload` fields without that fix. Complete EWS export acceptance therefore requires a later upstream merge and approved pin update.

nmap AnyConnect extracts the exact profile version; nmap IKE reports Cisco VPN Concentrator for the supplied Cisco vendor-ID signature.
