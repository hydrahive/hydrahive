#!/usr/bin/env bash
# OctopOS Installer - Modul 08: Ollama (Full Profile only)
# Installiert Ollama, richtet Systemd-Unit ein, zieht Default-Modelle.
# Idempotent: bereits installierte Version und Modelle werden übersprungen.

# Nur bei Full Profile ausführen
if [ "${PROFILE:-lite}" != "full" ]; then
  info "Ollama: Lite-Profil — überspringe Ollama-Installation"
  return 0
fi

info "Installiere Ollama (Full Profile)..."

# Idempotenz: Ollama bereits installiert?
if command -v ollama &>/dev/null; then
  OLLAMA_VER=$(ollama --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "?")
  info "Ollama $OLLAMA_VER bereits installiert — überprüfe Modelle..."
else
  info "Lade Ollama-Installer..."
  curl -fsSL https://ollama.ai/install.sh | sh
  success "Ollama installiert"
fi

# Systemd-Service sicherstellen
if ! systemctl is-enabled --quiet ollama 2>/dev/null; then
  systemctl enable ollama
fi
if ! systemctl is-active --quiet ollama; then
  systemctl start ollama
  sleep 5
fi

# Modelle ziehen (idempotent — ollama pull überspringt bereits vorhandene)
DEFAULT_MODELS=("llama3.2:3b" "llama3.1:8b")

for model in "${DEFAULT_MODELS[@]}"; do
  info "Prüfe Modell: $model"
  if ollama list 2>/dev/null | grep -qF "${model%%:*}"; then
    success "Modell $model bereits vorhanden"
  else
    info "Lade $model (kann einige Minuten dauern)..."
    ollama pull "$model" && success "Modell $model geladen" || warn "Modell $model konnte nicht geladen werden"
  fi
done

success "Ollama bereit ($(ollama list 2>/dev/null | grep -c ':' || echo '?') Modelle installiert)"
