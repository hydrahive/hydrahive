# Modul 02 — GPU-Erkennung (#45)
info "Erkenne GPU..."
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
  export PROFILE="full"
  success "GPU erkannt: $GPU_NAME -> Full Profile"
else
  export PROFILE="lite"
  warn "Kein NVIDIA GPU erkannt -> Lite Profile (Cloud-APIs only)"
fi
