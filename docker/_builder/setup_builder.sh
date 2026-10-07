#!/usr/bin/env bash
# Kept for the old calls: the builder setup is part of builder.sh now, which speaks
# through installer/lib/ui.sh. -y sets it up (builder.sh --setup), -u removes it again
# (builder.sh --uninstall), anything else shows the help of builder.sh.
myDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  -y) exec bash "${myDIR}/builder.sh" --setup ;;
  -u) exec bash "${myDIR}/builder.sh" --uninstall ;;
  *) exec bash "${myDIR}/builder.sh" -h ;;
esac
