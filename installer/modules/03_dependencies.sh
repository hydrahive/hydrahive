# Modul 03 — System-Dependencies (#46)
info "Installiere System-Dependencies..."

apt-get update -qq

DEPS=(python3 python3-pip python3-venv git curl samba nginx build-essential rsync sudo tree jq ffmpeg)

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

if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; then
  PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  success "Python $PY_VER OK"
else
  PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  error "Python 3.11+ benoetigt, gefunden: $PY_VER"
fi
