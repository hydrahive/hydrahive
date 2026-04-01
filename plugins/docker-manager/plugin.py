"""
docker-manager Plugin — Docker/Podman Container-Verwaltung als Agent-Tools.

Tools:
  - docker_ps:         Laufende Container listen
  - docker_ps_all:     Alle Container (inkl. gestoppte)
  - docker_logs:       Container-Logs abrufen
  - docker_start:      Container starten
  - docker_stop:       Container stoppen
  - docker_restart:    Container neustarten
  - docker_stats:      CPU/RAM/Netzwerk Statistiken
  - docker_inspect:    Container-Details (Ports, Volumes, Env)
  - docker_images:     Verfügbare Images listen
  - docker_compose_ps: Docker Compose Stack Status
"""
import subprocess
import json


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Führt einen Befehl aus und gibt (returncode, stdout, stderr) zurück."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", f"Befehl nicht gefunden: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, "", "Timeout"


def _docker_bin() -> str:
    """Findet docker oder podman."""
    for cmd in ["docker", "podman"]:
        rc, out, _ = _run(["which", cmd])
        if rc == 0:
            return cmd
    return "docker"  # Fallback, Fehlermeldung kommt dann von _run


def register(api):
    BIN = _docker_bin()

    @api.tool(
        tool_id="docker_ps",
        description="Zeigt alle laufenden Docker Container. Nutze dieses Tool um zu sehen welche Container aktiv sind.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def docker_ps(**_) -> str:
        rc, out, err = _run([BIN, "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"])
        if rc != 0:
            return f"Fehler: {err}"
        return out or "Keine laufenden Container"

    @api.tool(
        tool_id="docker_ps_all",
        description="Zeigt alle Docker Container (auch gestoppte). Nutze dieses Tool für eine Gesamtübersicht.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def docker_ps_all(**_) -> str:
        rc, out, err = _run([BIN, "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Size}}"])
        if rc != 0:
            return f"Fehler: {err}"
        return out or "Keine Container vorhanden"

    @api.tool(
        tool_id="docker_logs",
        description="Zeigt die letzten Logs eines Docker Containers. Nutze dieses Tool um Fehler in einem Container zu finden.",
        parameters={
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container-Name oder ID"},
                "lines": {"type": "integer", "description": "Anzahl Zeilen (default: 50)"},
                "since": {"type": "string", "description": "Zeitraum z.B. '5m', '1h', '2024-01-01' (optional)"},
            },
            "required": ["container"],
        },
    )
    def docker_logs(container: str, lines: int = 50, since: str = "", **_) -> str:
        cmd = [BIN, "logs", "--tail", str(min(lines, 500)), container]
        if since:
            cmd.insert(2, f"--since={since}")
        rc, out, err = _run(cmd, timeout=10)
        if rc != 0:
            return f"Fehler: {err}"
        return out or "(keine Logs)"

    @api.tool(
        tool_id="docker_start",
        description="Startet einen gestoppten Docker Container.",
        parameters={
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container-Name oder ID"},
            },
            "required": ["container"],
        },
    )
    def docker_start(container: str, **_) -> str:
        rc, out, err = _run([BIN, "start", container])
        if rc != 0:
            return f"Fehler: {err}"
        return f"Container '{container}' gestartet"

    @api.tool(
        tool_id="docker_stop",
        description="Stoppt einen laufenden Docker Container.",
        parameters={
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container-Name oder ID"},
            },
            "required": ["container"],
        },
    )
    def docker_stop(container: str, **_) -> str:
        rc, out, err = _run([BIN, "stop", container], timeout=30)
        if rc != 0:
            return f"Fehler: {err}"
        return f"Container '{container}' gestoppt"

    @api.tool(
        tool_id="docker_restart",
        description="Startet einen Docker Container neu.",
        parameters={
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container-Name oder ID"},
            },
            "required": ["container"],
        },
    )
    def docker_restart(container: str, **_) -> str:
        rc, out, err = _run([BIN, "restart", container], timeout=30)
        if rc != 0:
            return f"Fehler: {err}"
        return f"Container '{container}' neugestartet"

    @api.tool(
        tool_id="docker_stats",
        description="Zeigt CPU, RAM und Netzwerk-Statistiken aller laufenden Container. Ein Snapshot, kein Live-Stream.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def docker_stats(**_) -> str:
        rc, out, err = _run([BIN, "stats", "--no-stream", "--format",
                             "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}"])
        if rc != 0:
            return f"Fehler: {err}"
        return out or "Keine laufenden Container"

    @api.tool(
        tool_id="docker_inspect",
        description="Zeigt Details eines Containers: Ports, Volumes, Umgebungsvariablen, Netzwerk, Status.",
        parameters={
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container-Name oder ID"},
            },
            "required": ["container"],
        },
    )
    def docker_inspect(container: str, **_) -> str:
        rc, out, err = _run([BIN, "inspect", container])
        if rc != 0:
            return f"Fehler: {err}"
        try:
            data = json.loads(out)
            if not data:
                return "Container nicht gefunden"
            c = data[0]
            state = c.get("State", {})
            config = c.get("Config", {})
            network = c.get("NetworkSettings", {})
            mounts = c.get("Mounts", [])

            lines = [
                f"**{c.get('Name', '').lstrip('/')}**",
                f"Image: {config.get('Image', '?')}",
                f"Status: {state.get('Status', '?')} (Pid: {state.get('Pid', '?')})",
                f"Gestartet: {state.get('StartedAt', '?')}",
                "",
                "**Ports:**",
            ]
            ports = network.get("Ports", {})
            if ports:
                for port, bindings in ports.items():
                    if bindings:
                        for b in bindings:
                            lines.append(f"  {b.get('HostIp', '')}:{b.get('HostPort', '')} → {port}")
                    else:
                        lines.append(f"  {port} (nicht gemappt)")
            else:
                lines.append("  Keine")

            lines.append("")
            lines.append("**Volumes:**")
            if mounts:
                for m in mounts:
                    lines.append(f"  {m.get('Source', '?')} → {m.get('Destination', '?')} ({m.get('Type', '?')})")
            else:
                lines.append("  Keine")

            lines.append("")
            lines.append("**Umgebungsvariablen:**")
            env = config.get("Env", [])
            # Sensible Vars maskieren
            sensitive = {"password", "secret", "token", "key", "pass"}
            for e in env[:20]:
                k = e.split("=")[0] if "=" in e else e
                if any(s in k.lower() for s in sensitive):
                    lines.append(f"  {k}=***")
                else:
                    lines.append(f"  {e}")
            if len(env) > 20:
                lines.append(f"  ... und {len(env) - 20} weitere")

            return "\n".join(lines)
        except json.JSONDecodeError:
            return out[:3000]

    @api.tool(
        tool_id="docker_images",
        description="Zeigt alle verfügbaren Docker Images mit Größe und Tag.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    def docker_images(**_) -> str:
        rc, out, err = _run([BIN, "images", "--format", "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}"])
        if rc != 0:
            return f"Fehler: {err}"
        return out or "Keine Images vorhanden"

    @api.tool(
        tool_id="docker_compose_ps",
        description="Zeigt den Status eines Docker Compose Stacks. Muss im Verzeichnis mit docker-compose.yml ausgeführt werden.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Pfad zum Compose-Verzeichnis (default: aktuelles Verzeichnis)"},
            },
            "required": [],
        },
    )
    def docker_compose_ps(path: str = ".", **_) -> str:
        # docker compose oder docker-compose
        for compose_cmd in [[BIN, "compose", "ps"], ["docker-compose", "ps"]]:
            rc, out, err = _run(compose_cmd + (["-f", f"{path}/docker-compose.yml"] if path != "." else []))
            if rc == 0:
                return out or "Keine Compose-Services gefunden"
        return f"Docker Compose nicht verfügbar oder kein docker-compose.yml in {path}"
