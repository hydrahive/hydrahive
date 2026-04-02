#!/usr/bin/env bash
# Wrapper: wird vom Extension-Manager aufgerufen
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${INSTALLER_DIR}/modules/18_voice.sh"
