"""
tor-manager Plugin — Tor Control Protocol (Port 9051).

Tools:
  - tor_status:       Tor-Status und allgemeine Informationen
  - tor_circuits:     Aktive Circuits auflisten
  - tor_new_identity: Neue Tor-Identität anfordern (NEWNYM)
  - tor_resolve:      Hostnamen über Tor auflösen
"""
import json
import socket


def _load_config(username: str, plugin_id: str = "tor-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


class TorControl:
    """Minimal Tor Control Protocol client over raw socket."""

    def __init__(self, host: str, port: int, password: str = ""):
        self.host = host
        self.port = int(port)
        self.password = password
        self._sock = None

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=10)
        # Authenticate
        if self.password:
            self._send(f'AUTHENTICATE "{self.password}"\r\n')
        else:
            self._send("AUTHENTICATE\r\n")
        resp = self._recv()
        if not resp.startswith("250"):
            raise ConnectionError(f"Auth fehlgeschlagen: {resp}")

    def _send(self, data: str):
        self._sock.sendall(data.encode())

    def _recv(self) -> str:
        buf = b""
        while True:
            chunk = self._sock.recv(4096)
            buf += chunk
            decoded = buf.decode(errors="replace")
            # Response ends when we get a line starting with 3-digit code + space
            lines = decoded.splitlines()
            if lines and len(lines[-1]) >= 4 and lines[-1][3] == " ":
                return decoded
            if not chunk:
                return decoded

    def getinfo(self, key: str) -> str:
        self._send(f"GETINFO {key}\r\n")
        return self._recv()

    def signal(self, sig: str) -> str:
        self._send(f"SIGNAL {sig}\r\n")
        return self._recv()

    def resolve(self, hostname: str) -> str:
        self._send(f"RESOLVE {hostname}\r\n")
        return self._recv()

    def close(self):
        if self._sock:
            try:
                self._send("QUIT\r\n")
            except Exception:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


def _parse_value(response: str, key: str) -> str:
    """Extract value from GETINFO response."""
    for line in response.splitlines():
        if line.startswith(f"250-{key}=") or line.startswith(f"250 {key}="):
            return line.split("=", 1)[1].strip()
    return ""


def register(api):

    @api.tool(
        tool_id="tor_status",
        description="Zeigt Tor-Status: Version, Uptime, Bootstrapping-Fortschritt, Traffic und Netzwerk-Informationen.",
        parameters={
            "type": "object",
            "properties": {
                "control_host": {"type": "string", "description": "Tor Control Host (optional, sonst aus Config)"},
                "control_port": {"type": "integer", "description": "Tor Control Port (optional, sonst aus Config)"},
                "control_password": {"type": "string", "description": "Tor Control Password (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def tor_status(control_host: str = "", control_port: int = 0, control_password: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        host = control_host or cfg.get("control_host", "127.0.0.1")
        port = control_port or int(cfg.get("control_port", 9051))
        password = control_password or cfg.get("control_password", "")
        try:
            with TorControl(host, port, password) as tc:
                version_resp = tc.getinfo("version")
                bootstrap_resp = tc.getinfo("status/bootstrap-phase")
                traffic_read_resp = tc.getinfo("traffic/read")
                traffic_written_resp = tc.getinfo("traffic/written")
                uptime_resp = tc.getinfo("uptime")
                circuits_resp = tc.getinfo("circuit-status")

            version = _parse_value(version_resp, "version")
            bootstrap = _parse_value(bootstrap_resp, "status/bootstrap-phase")
            traffic_read = _parse_value(traffic_read_resp, "traffic/read")
            traffic_written = _parse_value(traffic_written_resp, "traffic/written")
            uptime = _parse_value(uptime_resp, "uptime")

            # Parse bootstrap progress
            bootstrap_pct = ""
            for part in bootstrap.split():
                if part.startswith("PROGRESS="):
                    bootstrap_pct = part.split("=")[1]

            # Count circuits
            circuit_count = len([l for l in circuits_resp.splitlines() if l.startswith("250-") and "BUILT" in l])

            def fmt_bytes(b_str: str) -> str:
                try:
                    b = int(b_str)
                    if b >= 1024**3:
                        return f"{b/1024**3:.2f} GB"
                    if b >= 1024**2:
                        return f"{b/1024**2:.1f} MB"
                    return f"{b/1024:.1f} KB"
                except Exception:
                    return b_str

            def fmt_uptime(s_str: str) -> str:
                try:
                    s = int(s_str)
                    d, rem = divmod(s, 86400)
                    h, rem = divmod(rem, 3600)
                    m, sec = divmod(rem, 60)
                    parts = []
                    if d:
                        parts.append(f"{d}d")
                    if h:
                        parts.append(f"{h}h")
                    parts.append(f"{m}m {sec}s")
                    return " ".join(parts)
                except Exception:
                    return s_str

            lines = [
                "**Tor Status:**",
                "",
                f"Version:     {version}",
                f"Uptime:      {fmt_uptime(uptime)}",
                f"Bootstrap:   {bootstrap_pct}%",
                f"Circuits:    {circuit_count} aktiv",
                f"Traffic RX:  {fmt_bytes(traffic_read)}",
                f"Traffic TX:  {fmt_bytes(traffic_written)}",
            ]
            return "\n".join(lines)
        except ConnectionError as e:
            return f"Verbindungsfehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="tor_circuits",
        description="Listet alle aktiven Tor-Circuits mit Pfad (Guard → Middle → Exit) auf.",
        parameters={
            "type": "object",
            "properties": {
                "control_host": {"type": "string", "description": "Tor Control Host (optional, sonst aus Config)"},
                "control_port": {"type": "integer", "description": "Tor Control Port (optional, sonst aus Config)"},
                "control_password": {"type": "string", "description": "Tor Control Password (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def tor_circuits(control_host: str = "", control_port: int = 0, control_password: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        host = control_host or cfg.get("control_host", "127.0.0.1")
        port = control_port or int(cfg.get("control_port", 9051))
        password = control_password or cfg.get("control_password", "")
        try:
            with TorControl(host, port, password) as tc:
                resp = tc.getinfo("circuit-status")
            lines_raw = resp.splitlines()
            circuits = []
            for line in lines_raw:
                # Lines like: 250-<id> BUILT <path> BUILD_FLAGS=...
                if line.startswith("250-") or (line.startswith("250 ") and "BUILT" in line):
                    parts = line[4:].split()
                    if len(parts) >= 3:
                        cid = parts[0]
                        status = parts[1]
                        path_raw = parts[2] if len(parts) > 2 else ""
                        # Path is comma-separated fingerprints with optional names
                        path_nodes = []
                        for node in path_raw.split(","):
                            # node format: $FINGERPRINT~Name or $FINGERPRINT
                            if "~" in node:
                                path_nodes.append(node.split("~")[1])
                            elif node.startswith("$") and len(node) > 10:
                                path_nodes.append(node[1:11] + "...")
                            else:
                                path_nodes.append(node)
                        circuits.append((cid, status, " → ".join(path_nodes)))
            built = [(c, s, p) for c, s, p in circuits if s == "BUILT"]
            if not built:
                return "Keine aktiven Circuits"
            out_lines = [f"**Tor Circuits ({len(built)} aktiv):**", ""]
            for cid, status, path in built[:30]:
                out_lines.append(f"  #{cid:>4}  {path}")
            if len(built) > 30:
                out_lines.append(f"  ... und {len(built) - 30} weitere")
            return "\n".join(out_lines)
        except ConnectionError as e:
            return f"Verbindungsfehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="tor_new_identity",
        description="Fordert eine neue Tor-Identität an (NEWNYM). Alle zukünftigen Verbindungen nutzen neue Circuits.",
        parameters={
            "type": "object",
            "properties": {
                "control_host": {"type": "string", "description": "Tor Control Host (optional, sonst aus Config)"},
                "control_port": {"type": "integer", "description": "Tor Control Port (optional, sonst aus Config)"},
                "control_password": {"type": "string", "description": "Tor Control Password (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def tor_new_identity(control_host: str = "", control_port: int = 0, control_password: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        host = control_host or cfg.get("control_host", "127.0.0.1")
        port = control_port or int(cfg.get("control_port", 9051))
        password = control_password or cfg.get("control_password", "")
        try:
            with TorControl(host, port, password) as tc:
                resp = tc.signal("NEWNYM")
            if "250" in resp:
                return "Neue Tor-Identität angefordert. Alle zukünftigen Verbindungen nutzen neue Circuits.\n(Hinweis: Es gilt eine Rate-Limiting von 10 Sekunden zwischen NEWNYM-Signalen.)"
            return f"Antwort: {resp.strip()}"
        except ConnectionError as e:
            return f"Verbindungsfehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="tor_resolve",
        description="Löst einen Hostnamen über das Tor-Netzwerk auf (anonyme DNS-Auflösung).",
        parameters={
            "type": "object",
            "properties": {
                "hostname": {"type": "string", "description": "Hostname der aufgelöst werden soll"},
                "control_host": {"type": "string", "description": "Tor Control Host (optional, sonst aus Config)"},
                "control_port": {"type": "integer", "description": "Tor Control Port (optional, sonst aus Config)"},
                "control_password": {"type": "string", "description": "Tor Control Password (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["hostname"],
        },
    )
    def tor_resolve(hostname: str, control_host: str = "", control_port: int = 0, control_password: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        host = control_host or cfg.get("control_host", "127.0.0.1")
        port = control_port or int(cfg.get("control_port", 9051))
        password = control_password or cfg.get("control_password", "")
        try:
            with TorControl(host, port, password) as tc:
                resp = tc.resolve(hostname)
            # ADDRMAP response comes asynchronously; check for 250 OK first
            if "250" in resp:
                # Try to parse ADDRMAP from response
                for line in resp.splitlines():
                    if "ADDRMAP" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            return f"DNS-Auflösung über Tor:\n{hostname} → {parts[2]}"
                return f"Auflösungsanfrage für '{hostname}' gesendet.\nAntwort: {resp.strip()[:200]}"
            return f"Fehler bei Auflösung: {resp.strip()}"
        except ConnectionError as e:
            return f"Verbindungsfehler: {e}"
        except Exception as e:
            return f"Fehler: {e}"
