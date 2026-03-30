import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ShieldCheck, User, KeyRound, AlertCircle, CheckCircle2 } from "lucide-react";

export function InvitePage() {
  const API = "/api";
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();

  const [info,    setInfo]    = useState<{role:string;group:string;note:string}|null>(null);
  const [error,   setError]   = useState("");
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [saving,  setSaving]  = useState(false);
  const [done,    setDone]    = useState(false);

  useEffect(() => {
    if (!token) return;
    fetch(`${API}/invites/${token}`)
      .then(r => r.json())
      .then(d => {
        if (d.valid) setInfo(d);
        else setError(d.detail ?? "Ungültiger Link");
      })
      .catch(() => setError("Verbindungsfehler"))
      .finally(() => setLoading(false));
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== password2) { setError("Passwörter stimmen nicht überein"); return; }
    setSaving(true); setError("");
    try {
      const res = await fetch(`${API}/invites/${token}/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail ?? "Fehler beim Anlegen"); return; }
      setDone(true);
      setTimeout(() => navigate("/login"), 3000);
    } catch { setError("Verbindungsfehler"); }
    finally { setSaving(false); }
  }

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
    </div>
  );

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="flex justify-center">
            <div className="h-12 w-12 rounded-xl bg-primary/10 flex items-center justify-center">
              <ShieldCheck className="h-6 w-6 text-primary" />
            </div>
          </div>
          <h1 className="text-2xl font-bold">Willkommen bei HydraHive</h1>
          <p className="text-muted-foreground text-sm">Du wurdest eingeladen. Lege deinen Account an.</p>
        </div>

        {done ? (
          <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-6 text-center space-y-3">
            <CheckCircle2 className="h-10 w-10 text-green-500 mx-auto" />
            <p className="font-medium">Account erstellt!</p>
            <p className="text-sm text-muted-foreground">Du wirst in Kürze zum Login weitergeleitet…</p>
          </div>
        ) : (
          <div className="bg-card border rounded-xl p-6 space-y-5">
            {info?.note && (
              <p className="text-sm text-muted-foreground border-b pb-3">
                Einladungsnotiz: <span className="text-foreground font-medium">{info.note}</span>
              </p>
            )}

            {error && (
              <div className="flex items-center gap-2 bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {error}
              </div>
            )}

            {info ? (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                    <User className="h-3.5 w-3.5" />Benutzername
                  </label>
                  <input value={username} onChange={e => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""))}
                    placeholder="meinname" required minLength={3} maxLength={32}
                    className="w-full px-3 py-2.5 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
                  <p className="text-xs text-muted-foreground">Nur Kleinbuchstaben, Ziffern und _ erlaubt</p>
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                    <KeyRound className="h-3.5 w-3.5" />Passwort
                  </label>
                  <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                    placeholder="Mindestens 8 Zeichen" required minLength={8}
                    className="w-full px-3 py-2.5 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Passwort bestätigen</label>
                  <input type="password" value={password2} onChange={e => setPassword2(e.target.value)}
                    placeholder="Passwort wiederholen" required minLength={8}
                    className="w-full px-3 py-2.5 text-sm border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
                </div>

                <div className="flex items-center gap-2 bg-muted/50 rounded-lg px-3 py-2 text-xs text-muted-foreground">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Rolle: <span className="text-foreground font-medium">{info.role}</span>
                  &nbsp;·&nbsp; Gruppe: <span className="text-foreground font-medium">{info.group}</span>
                </div>

                <button type="submit" disabled={saving}
                  className="w-full py-2.5 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
                  {saving ? "Wird angelegt…" : "Account erstellen"}
                </button>
              </form>
            ) : (
              <div className="text-center py-4 text-muted-foreground text-sm">
                {error || "Dieser Einladungslink ist nicht mehr gültig."}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
