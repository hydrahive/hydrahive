import { useEffect, useState } from "react";
import { KeyRound, Plus, Trash2, Eye, EyeOff, Save, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

interface SecretMeta {
  name: string;
  masked: string;
  has_value: boolean;
}

export function SecretsPage() {
  const { t } = useTranslation();
  const [secrets, setSecrets]   = useState<SecretMeta[]>([]);
  const [loading, setLoading]   = useState(true);
  const [newName, setNewName]   = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving]     = useState(false);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [revealLoading, setRevealLoading] = useState<string | null>(null);
  const [toast, setToast]       = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.get("/admin/agent-secrets") as SecretMeta[];
      setSecrets(data);
    } catch {
      setSecrets([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!newName.trim() || !newValue.trim()) return;
    setSaving(true);
    try {
      await api.put(`/admin/agent-secrets/${newName.trim()}`, { value: newValue.trim() });
      setNewName("");
      setNewValue("");
      showToast("Secret gespeichert");
      await load();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Secret "${name}" wirklich löschen?`)) return;
    await api.delete(`/admin/agent-secrets/${name}`);
    setRevealed(r => { const n = { ...r }; delete n[name]; return n; });
    showToast(`"${name}" gelöscht`);
    await load();
  };

  const handleReveal = async (name: string) => {
    if (revealed[name] !== undefined) {
      setRevealed(r => { const n = { ...r }; delete n[name]; return n; });
      return;
    }
    setRevealLoading(name);
    try {
      const data = await api.get(`/admin/agent-secrets/${name}/reveal`) as { value: string };
      setRevealed(r => ({ ...r, [name]: data.value }));
    } finally {
      setRevealLoading(null);
    }
  };

  return (
    <div className="max-w-2xl mx-auto py-8 px-4 flex flex-col gap-6">
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-xl bg-green-900/90 border border-green-500/40 px-4 py-2.5 text-sm text-green-200 shadow-xl">
          {toast}
        </div>
      )}

      <div className="flex items-center gap-3">
        <KeyRound className="h-5 w-5 text-white/50" />
        <div>
          <h1 className="text-lg font-semibold text-white">Agent Secrets</h1>
          <p className="text-xs text-white/40">Zugangsdaten für Agenten-Tools (get_secret). Nur von Admins einsehbar.</p>
        </div>
      </div>

      {/* Add new */}
      <div className="card flex flex-col gap-3">
        <p className="text-xs font-semibold text-white/40 uppercase tracking-widest">Neues Secret</p>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Name (z.B. github_token)"
            value={newName}
            onChange={e => setNewName(e.target.value.replace(/[^a-zA-Z0-9_-]/g, "_"))}
            className="flex-1 rounded-lg bg-zinc-900 border border-white/15 px-3 py-2 text-sm text-white placeholder-white/25 focus:outline-none focus:border-white/30"
          />
          <input
            type="password"
            placeholder="Wert"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAdd()}
            className="flex-1 rounded-lg bg-zinc-900 border border-white/15 px-3 py-2 text-sm text-white placeholder-white/25 focus:outline-none focus:border-white/30"
          />
          <button
            type="button"
            onClick={handleAdd}
            disabled={saving || !newName.trim() || !newValue.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-2 text-sm text-white transition-colors"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            <span>{t("common.add")}</span>
          </button>
        </div>
        <p className="text-[11px] text-white/25">
          Name: nur Buchstaben, Zahlen, _ und -. Agenten verwenden <code className="text-cyan-400">get_secret(name="...")</code> zum Lesen.
        </p>
      </div>

      {/* List */}
      <div className="card flex flex-col gap-0 divide-y divide-white/5">
        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-white/30" />
          </div>
        ) : secrets.length === 0 ? (
          <p className="text-sm text-white/30 text-center py-8">Noch keine Secrets gespeichert.</p>
        ) : (
          secrets.map(s => (
            <div key={s.name} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
              <KeyRound className="h-4 w-4 text-white/30 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-white truncate">{s.name}</p>
                <p className={cn(
                  "text-xs font-mono mt-0.5",
                  revealed[s.name] ? "text-amber-300" : "text-white/25"
                )}>
                  {revealed[s.name] ?? s.masked}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleReveal(s.name)}
                className="p-1.5 rounded-lg hover:bg-white/10 text-white/30 hover:text-white/70 transition-colors"
                title={revealed[s.name] ? "Verbergen" : "Anzeigen"}
              >
                {revealLoading === s.name
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : revealed[s.name] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
              <button
                type="button"
                onClick={() => handleDelete(s.name)}
                className="p-1.5 rounded-lg hover:bg-red-900/40 text-white/20 hover:text-red-400 transition-colors"
                title={t("common.delete")}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))
        )}
      </div>

      <div className="rounded-xl border border-white/8 bg-white/3 px-4 py-3">
        <p className="text-xs text-white/35 leading-relaxed">
          <strong className="text-white/55">Hinweis:</strong> Secrets werden in <code className="text-cyan-400">/etc/hydrahive/agent_secrets.json</code> gespeichert (chmod 600).
          Agenten mit dem <code className="text-cyan-400">vault</code>-Permission können darauf zugreifen.
          Nutze <strong className="text-white/55">Vaultwarden</strong> unter <a href="/vault/" target="_blank" className="text-indigo-400 hover:underline">/vault/</a> für die persönliche Passwortverwaltung.
        </p>
      </div>
    </div>
  );
}
