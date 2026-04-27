# Modul 03 — System-Dependencies (#46)
info "Installiere System-Dependencies..."

# GitHub CLI apt-Repo einrichten (gh ist nicht im Ubuntu-Standard-Repo)
if ! dpkg -l gh 2>/dev/null | grep -q "^ii"; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
fi

apt-get update -qq

DEPS=(python3 python3-pip python3-venv openssh-client git git-lfs curl samba nginx build-essential rsync sudo tree jq ffmpeg gh)

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
  # BL-09: --no-install-recommends schneidet empfohlene Pakete weg (meist
  # GUI-Krempel wie cups, avahi etc.). Headless-Server brauchen das nicht.
  # Harte Depends bleiben erhalten.
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${MISSING[@]}"
  success "Dependencies installiert: ${MISSING[*]}"
fi

if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; then
  PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  success "Python $PY_VER OK"
else
  PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  error "Python 3.11+ benoetigt, gefunden: $PY_VER"
fi
