#!/usr/bin/env bash
# ensure_runtime_dirs.sh — idempotente Absicherung der Runtime-State-Pfade.
#
# Wird von sowohl installer/modules/06_core_service.sh (Initial-Install) als
# auch von installer/update.sh (vor `systemctl restart hydrahive-core`)
# gesourced. Wenn ein Feature einen neuen Pfad unter /var/lib/hydrahive/
# braucht, gehört er hier hin — damit existierende Instanzen beim Update
# die Verzeichnisse automatisch bekommen und der Core nicht in einen
# degradierten Zustand fällt.
#
# Exits non-zero nur, wenn die Helper-Variablen fehlen (Programmierfehler).
# mkdir/chown/chmod selbst laufen mit `|| true`, damit ein temporär
# read-only FS nicht den gesamten Update-Pfad killt — der Core bleibt
# dann im permission-toleranten Modus (siehe #687 + feedback_runtime_state_safety).
#
# Erwartete Umgebung:
#   HYDRAHIVE_USER   (optional, default "hydrahive")
#   HYDRAHIVE_GROUP  (optional, default = HYDRAHIVE_USER)

set -u

HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"
HYDRAHIVE_GROUP="${HYDRAHIVE_GROUP:-${HYDRAHIVE_USER}}"

_HYDRAHIVE_RUNTIME_DIRS=(
    /var/lib/hydrahive/worktrees   # #651 Sub-Agent-Worktrees
    /var/lib/hydrahive/users       # #659 per-User mutable Daten
    /var/lib/hydrahive/jobs        # #687 Async-Job-Fundament
    /var/lib/hydrahive/deleted-projects # gelöschte Projekte außerhalb /projects
    /var/lib/hydrahive/yjs         # Fresh-Install BL-05: Yjs-Store (collab_yjs)
    /var/log/hydrahive             # Fresh-Install BL-04: notification_service Log-DB + AMEM-Logs
)

# Uploads-Verzeichnis: world-readable (755) damit shell_exec in jedem Modus Zugriff hat.
_HYDRAHIVE_UPLOAD_DIRS=(
    /var/lib/hydrahive/uploads     # #960 Chat-Uploads (me-chat)
)

ensure_hydrahive_runtime_dirs() {
    local _dir
    for _dir in "${_HYDRAHIVE_RUNTIME_DIRS[@]}"; do
        mkdir -p "${_dir}" 2>/dev/null || true
        chown "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" "${_dir}" 2>/dev/null || true
        chmod 750 "${_dir}" 2>/dev/null || true
    done
    for _dir in "${_HYDRAHIVE_UPLOAD_DIRS[@]}"; do
        mkdir -p "${_dir}" 2>/dev/null || true
        chown "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" "${_dir}" 2>/dev/null || true
        chmod 755 "${_dir}" 2>/dev/null || true
    done
}

# Beim direkten Sourcen auch ausführen — Caller muss die Funktion nicht
# nochmal rufen. Idempotent.
ensure_hydrahive_runtime_dirs
