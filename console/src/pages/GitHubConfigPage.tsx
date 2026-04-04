import { useEffect, useState } from "react";
import { Github, CheckCircle2, AlertCircle, Trash2, ExternalLink, Lock, Globe } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface TokenStatus {
  configured: boolean;
  login?: string;
  name?: string;
  avatar_url?: string;
  html_url?: string;
  scopes?: string[];
}

export function GitHubConfigPage() {
  const { t } = useTranslation();
  const [status,  setStatus]  = useState<TokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [token,   setToken]   = useState("");
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState("");
  const [success, setSuccess] = useState("");
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function loadStatus() {
    try {
      const d = await api.githubTokenStatus() as unknown as TokenStatus;
      setStatus(d);
    } catch {
      setStatus({ configured: false });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadStatus(); }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setSaving(true); setError(""); setSuccess("");
    try {
      const d = await api.saveGithubToken(token.trim()) as unknown as TokenStatus & { login?: string };
      setToken("");
      setSuccess(`Token gespeichert — verbunden als @${d.login}`);
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  }

  function handleDelete() {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("confirm.deleteGithubToken"),
      action: async () => {
        setError(""); setSuccess("");
        try {
          await api.deleteGithubToken();
          setStatus({ configured: false });
          setSuccess("Token entfernt");
        } catch {
          setError("Fehler beim Entfernen");
        }
      },
    });
  }

  if (loading) return (
    <div className="p-6 flex items-center justify-center">
      <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary" />
    </div>
  );

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-lg bg-zinc-800 flex items-center justify-center">
          <Github className="h-5 w-5 text-zinc-300" />
        </div>
        <div>
          <h2 className="text-base font-semibold">GitHub-Integration</h2>
          <p className="text-sm text-muted-foreground">Personal Access Token (PAT) für Repo-Zugriff</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}
      {success && (
        <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/20 rounded-lg px-4 py-3 text-sm text-green-400">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          {success}
        </div>
      )}

      {/* Status-Card */}
      {status?.configured && status.login ? (
        <div className="bg-card border rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {status.avatar_url && (
                <img src={status.avatar_url} alt={status.login} className="h-10 w-10 rounded-full" />
              )}
              <div>
                <p className="font-medium">{status.name || status.login}</p>
                <a href={status.html_url} target="_blank" rel="noreferrer"
                  className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                  @{status.login} <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            </div>
            <button onClick={handleDelete}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-destructive border border-destructive/30 rounded-lg hover:bg-destructive/10 transition-colors">
              <Trash2 className="h-3.5 w-3.5" /> Entfernen
            </button>
          </div>

          {status.scopes && status.scopes.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border">
              {status.scopes.map(s => (
                <span key={s} className="px-2 py-0.5 text-xs bg-secondary rounded-full font-mono">{s}</span>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="bg-card border rounded-xl p-5">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-4">
            <AlertCircle className="h-4 w-4" />
            Kein GitHub-Token konfiguriert
          </div>

          <form onSubmit={handleSave} className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Personal Access Token
              </label>
              <input
                type="password"
                value={token}
                onChange={e => setToken(e.target.value)}
                placeholder="ghp_..."
                required
                className="w-full px-3 py-2.5 text-sm border rounded-lg bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <p className="text-xs text-muted-foreground">
                Benötigt: <code className="bg-secondary px-1 rounded">repo</code> oder{" "}
                <code className="bg-secondary px-1 rounded">public_repo</code> Scope
              </p>
            </div>
            <button type="submit" disabled={saving || !token.trim()}
              className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? "Wird getestet…" : "Token speichern & testen"}
            </button>
          </form>
        </div>
      )}

      {/* Hinweis */}
      <div className="bg-muted/40 border rounded-xl p-4 space-y-2 text-xs text-muted-foreground">
        <p className="font-medium text-foreground text-sm">Token erstellen auf GitHub</p>
        <ol className="space-y-1 list-decimal list-inside">
          <li>GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)</li>
          <li>„Generate new token (classic)"</li>
          <li>Scopes: <code className="bg-background px-1 rounded">repo</code> (für private Repos) oder <code className="bg-background px-1 rounded">public_repo</code></li>
        </ol>
        <div className="flex items-center gap-4 pt-2">
          <span className="flex items-center gap-1"><Lock className="h-3 w-3" /> Private Repos: <code>repo</code></span>
          <span className="flex items-center gap-1"><Globe className="h-3 w-3" /> Nur public: <code>public_repo</code></span>
        </div>
      </div>
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}
