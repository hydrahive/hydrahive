"""ssl-cert-checker — SSL-Zertifikate überwachen."""
import ssl, socket, datetime

def register(api):
    @api.tool(tool_id="check_cert", description="Prüft das SSL-Zertifikat einer Domain. Zeigt Ablaufdatum, Aussteller, Restlaufzeit.",
        parameters={"type":"object","properties":{"domain":{"type":"string","description":"Domain (z.B. hydrahive.org)"},"port":{"type":"integer","description":"Port (default: 443)"}},"required":["domain"]})
    def check_cert(domain:str, port:int=443, **_) -> str:
        try:
            ctx=ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                s.settimeout(10); s.connect((domain, port))
                cert=s.getpeercert()
            expires=datetime.datetime.strptime(cert["notAfter"],"%b %d %H:%M:%S %Y %Z")
            days_left=(expires-datetime.datetime.utcnow()).days
            issuer=dict(x[0] for x in cert.get("issuer",()))
            subject=dict(x[0] for x in cert.get("subject",()))
            status="✅ OK" if days_left>30 else "⚠ Läuft bald ab!" if days_left>0 else "❌ ABGELAUFEN!"
            return f"{status}\n\nDomain: {domain}:{port}\nSubject: {subject.get('commonName','?')}\nAussteller: {issuer.get('organizationName','?')}\nGültig bis: {expires.strftime('%Y-%m-%d')}\nRestlaufzeit: {days_left} Tage"
        except Exception as e:
            return f"Fehler bei {domain}:{port}: {e}"

    @api.tool(tool_id="cert_overview", description="Prüft mehrere Domains auf einmal.",
        parameters={"type":"object","properties":{"domains":{"type":"string","description":"Komma-getrennte Domains (z.B. 'hydrahive.org,github.com')"}},"required":["domains"]})
    def cert_overview(domains:str, **_) -> str:
        results=[]
        for d in domains.split(","):
            d=d.strip()
            if not d: continue
            try:
                ctx=ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(),server_hostname=d) as s:
                    s.settimeout(10); s.connect((d,443))
                    cert=s.getpeercert()
                expires=datetime.datetime.strptime(cert["notAfter"],"%b %d %H:%M:%S %Y %Z")
                days=(expires-datetime.datetime.utcnow()).days
                icon="✅" if days>30 else "⚠" if days>0 else "❌"
                results.append(f"{icon} {d}: {days} Tage ({expires.strftime('%Y-%m-%d')})")
            except Exception as e:
                results.append(f"❌ {d}: {e}")
        return "\n".join(results) or "Keine Domains angegeben"
