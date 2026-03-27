#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Ollama..."

systemctl stop ollama    2>/dev/null || true
systemctl disable ollama 2>/dev/null || true
rm -f /etc/systemd/system/ollama.service
systemctl daemon-reload

rm -f /usr/local/bin/ollama
rm -rf /usr/share/ollama

warn "Ollama-Modelle unter /root/.ollama bzw. ~/.ollama wurden NICHT gelöscht (können groß sein)."
warn "Manuell entfernen: sudo rm -rf ~/.ollama"

success "Ollama deinstalliert"
