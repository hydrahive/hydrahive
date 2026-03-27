#!/usr/bin/env bash
# HydraHive Installer - Modul 05: Matrix Admin-Account
# Legt @admin:<server_name> an und verifiziert Admin-Room-Mitgliedschaft.
# Idempotent: bereits existierender Account wird nur neu eingeloggt.

CONDUWUIT_URL="http://127.0.0.1:6167"
CONDUWUIT_TOML="/etc/conduwuit/conduwuit.toml"

# --- Config lesen ---
SERVER_NAME=$(grep -E '^server_name\s*=' "$CONDUWUIT_TOML" | sed 's/.*=\s*"\(.*\)"/\1/')
REG_TOKEN=$(grep -E '^registration_token\s*=' "$CONDUWUIT_TOML" | sed 's/.*=\s*"\(.*\)"/\1/')

[ -z "$SERVER_NAME" ] && error "server_name nicht in $CONDUWUIT_TOML gefunden"
[ -z "$REG_TOKEN" ]   && error "registration_token nicht in $CONDUWUIT_TOML gefunden"

ADMIN_USER="admin"
ADMIN_MXID="@${ADMIN_USER}:${SERVER_NAME}"

# Admin-Passwort aus Datei lesen oder neu generieren
CRED_FILE="/etc/hydrahive/admin_credentials"
if [ -f "$CRED_FILE" ]; then
    ADMIN_PASS=$(grep -E '^matrix_admin_password=' "$CRED_FILE" | cut -d= -f2-)
fi
if [ -z "${ADMIN_PASS:-}" ]; then
    _raw="$(openssl rand -base64 40)"; _clean="${_raw//[\/+=]/}"; ADMIN_PASS="${_clean:0:32}"
    mkdir -p /etc/hydrahive
    echo "matrix_admin_password=${ADMIN_PASS}" >> "$CRED_FILE"
    chmod 600 "$CRED_FILE"
    info "Neues Admin-Passwort generiert und in $CRED_FILE gespeichert"
fi

# --- Registrierung versuchen ---
REG_RESP=$(curl -s -X POST "${CONDUWUIT_URL}/_matrix/client/v3/register" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\",\"auth\":{\"type\":\"m.login.registration_token\",\"token\":\"${REG_TOKEN}\"},\"inhibit_login\":false}")

ERRCODE=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('errcode',''))" 2>/dev/null)

if echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'access_token' in d else 1)" 2>/dev/null; then
    success "Matrix Admin-Account '${ADMIN_MXID}' angelegt"
    ACCESS_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
elif [ "$ERRCODE" = "M_USER_IN_USE" ]; then
    info "Account '${ADMIN_MXID}' existiert bereits — logge ein..."
    LOGIN_RESP=$(curl -s -X POST "${CONDUWUIT_URL}/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"${ADMIN_MXID}\"},\"password\":\"${ADMIN_PASS}\"}")
    if echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'access_token' in d else 1)" 2>/dev/null; then
        success "Login als '${ADMIN_MXID}' erfolgreich"
        ACCESS_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    else
        error "Login fehlgeschlagen: $LOGIN_RESP"
    fi
else
    error "Registrierung fehlgeschlagen: $REG_RESP"
fi

# --- Admin-Room prüfen ---
ROOMS_RESP=$(curl -s "${CONDUWUIT_URL}/_matrix/client/v3/joined_rooms" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}")
ROOM_COUNT=$(echo "$ROOMS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('joined_rooms',[])))" 2>/dev/null)

if [ "${ROOM_COUNT:-0}" -ge 1 ]; then
    success "Admin-User ist Mitglied im Admin-Room — Server-Admin bestaetigt"
else
    warn "Admin-Room nicht gefunden — ggf. erster Account war ein anderer User"
fi

info "Admin-MXID:  ${ADMIN_MXID}"
info "Credentials: sudo cat ${CRED_FILE}"
