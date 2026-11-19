# nginx image

The image requires Alpine nginx 1.30.7-r0 or later, which fixes the five
nginx2root vulnerabilities (CVE-2026-123007, CVE-2026-123633,
CVE-2026-123634, CVE-2026-121810, and CVE-2026-113425). HTTP/2 remains
enabled. Disabling HTTP/2 alone does not address all five vulnerabilities.
The package requirement makes the build fail if a fixed package is unavailable.

Existing images and running containers are not updated by this change: rebuild
and distribute the nginx image, then recreate the container. Host kernel updates
must be managed separately.
