#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-}"

case "$PROFILE" in
  full|minimal|lan) ;;
  *)
    echo "usage: $0 <full|minimal|lan>" >&2
    exit 2
    ;;
esac

ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null

if [ "$PROFILE" = "full" ]; then
  ufw --force disable >/dev/null
  exit 0
fi

ufw allow 22/tcp comment 'OctopOS SSH' >/dev/null
ufw allow 80/tcp comment 'OctopOS HTTP' >/dev/null
ufw allow 3002/tcp comment 'OctopOS Gitea' >/dev/null
ufw allow 8008/tcp comment 'OctopOS Matrix HTTPS' >/dev/null

if [ "$PROFILE" = "lan" ]; then
  ufw allow 139/tcp comment 'OctopOS SMB NetBIOS' >/dev/null
  ufw allow 445/tcp comment 'OctopOS SMB' >/dev/null
  ufw allow 137/udp comment 'OctopOS NetBIOS NS' >/dev/null
  ufw allow 138/udp comment 'OctopOS NetBIOS DGM' >/dev/null
fi

ufw --force enable >/dev/null
