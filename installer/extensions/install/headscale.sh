#!/usr/bin/env bash
# Wrapper für Headscale-Installation via Extension Manager
# Setzt VPN_MODE=headscale damit kein interaktiver Prompt kommt
set -euo pipefail

export VPN_MODE="headscale"
export HYDRAHIVE_DIR="${HYDRAHIVE_DIR:-/opt/hydrahive}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/../../modules/12_vpn.sh"
