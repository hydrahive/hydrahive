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

ufw allow 22/tcp comment 'HydraHive SSH' >/dev/null
ufw allow 80/tcp comment 'HydraHive HTTP' >/dev/null
ufw allow 3002/tcp comment 'HydraHive Gitea' >/dev/null
ufw allow 8008/tcp comment 'HydraHive Matrix HTTPS' >/dev/null

if [ "$PROFILE" = "lan" ]; then
  ufw allow 139/tcp comment 'HydraHive SMB NetBIOS' >/dev/null
  ufw allow 445/tcp comment 'HydraHive SMB' >/dev/null
  ufw allow 137/udp comment 'HydraHive NetBIOS NS' >/dev/null
  ufw allow 138/udp comment 'HydraHive NetBIOS DGM' >/dev/null
fi

ufw --force enable >/dev/null
