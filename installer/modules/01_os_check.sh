# Modul 01 — OS-Check (#44)
info "Pruefe Betriebssystem..."
if [ -f /etc/os-release ]; then
  . /etc/os-release
  case "$ID-$VERSION_ID" in
    ubuntu-22.04|ubuntu-24.04|debian-12)
      success "OS: $PRETTY_NAME" ;;
    *)
      error "Nicht unterstuetzt: $PRETTY_NAME. Bitte Ubuntu 22.04/24.04 oder Debian 12 verwenden." ;;
  esac
else
  error "Betriebssystem konnte nicht erkannt werden."
fi
