#!/usr/bin/env bash
# OctopOS Installer
# Usage: curl -sSL https://get.octopos.io | bash
set -euo pipefail

OCTOPOS_VERSION="0.1.0"
OCTOPOS_DIR="/opt/octopos"

# Farben
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
BLUE="\033[0;34m"
NC="\033[0m"

info()    { echo -e "${BLUE}[OctopOS]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# OS-Check (#44)
check_os() {
  info "Prüfe Betriebssystem..."
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "$ID-$VERSION_ID" in
      ubuntu-22.04|ubuntu-24.04|debian-12) success "OS: $PRETTY_NAME" ;;
      *) error "Nicht unterstuetzt: $PRETTY_NAME. Bitte Ubuntu 22.04/24.04 oder Debian 12 verwenden." ;;
    esac
  else
    error "Betriebssystem konnte nicht erkannt werden."
  fi
}

# GPU-Erkennung (#45)
detect_gpu() {
  info "Erkenne GPU..."
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
    PROFILE="full"
    success "GPU erkannt: $GPU_NAME -> Full Profile"
  else
    PROFILE="lite"
    warn "Kein NVIDIA GPU erkannt -> Lite Profile (Cloud-APIs only)"
  fi
  export PROFILE
}

# System-Dependencies (#46)
install_dependencies() {
  info "Installiere System-Dependencies..."

  apt-get update -qq

  DEPS=(
    python3
    python3-pip
    python3-venv
    git
    curl
    samba
    nginx
    build-essential
  )

  MISSING=()
  for dep in "${DEPS[@]}"; do
    if ! dpkg -l "$dep" 2>/dev/null | grep -q "^ii"; then
      MISSING+=("$dep")
    fi
  done

  if [ ${#MISSING[@]} -eq 0 ]; then
    success "Alle Dependencies bereits installiert"
  else
    info "Installiere: ${MISSING[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
    success "Dependencies installiert: ${MISSING[*]}"
  fi

  # Python-Version prüfen (mind. 3.11)
  if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    success "Python $PY_VER OK"
  else
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    error "Python 3.11+ benoetigt, gefunden: $PY_VER"
  fi
}

# --- Main ---
check_os
detect_gpu
install_dependencies

echo ""
info "OctopOS $OCTOPOS_VERSION - Profil: $PROFILE"
success "Phase 1 Fundament - System-Dependencies installiert."
info "Naechster Schritt: Conduit (#41)"
