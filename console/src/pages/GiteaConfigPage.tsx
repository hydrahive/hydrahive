import { useState, useEffect } from "react";
import { api, GiteaConfig, GiteaRepo } from "../lib/api";
import { GitBranch, RefreshCw, Save, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

export default function GiteaConfigPage() {
  const { t } = useTranslation();
  const [config, setConfig]   = useState<GiteaConfig>({ url: "http://127.0.0.1:3001", token: "", org: "hydrahive", webhook_secret: "" });
  const [repos, setRepos]     = useState<GiteaRepo[]>([]);
  const [saving, setSaving]   = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg]         = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    try {
      const [cfgRes, reposRes] = await Promise.allSettled([
        api.giteaConfig(),
        api.giteaRepos(),
      ]);
      if (cfgRes.status === "fulfilled")   setConfig(cfgRes.value as GiteaConfig);
      if (reposRes.status === "fulfilled") setRepos((reposRes.value as { repos: GiteaRepo[] }).repos || []);
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      await api.updateGiteaConfig(config);
      setMsg({ ok: true, text: "Konfiguration gespeichert." });
      await load();
    } catch (e: unknown) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setSaving(false);
    }
  }

  const externalUrl = config.url.replace("127.0.0.1", window.location.hostname).replace(":3001", ":3002");

  return (
    <div className="p-6">
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <GitBranch className="w-6 h-6 text-emerald-400" />
          <div>
            <h1 className="text-xl font-bold text-white">Gitea-Konfiguration</h1>
            <p className="text-xs text-gray-400">{t("pageDesc.gitea")}</p>
          </div>
          <a
            href={externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
          >
            Gitea öffnen <ExternalLink className="w-3 h-3" />
          </a>
        </div>

        {loading ? (
          <p className="text-gray-400 text-sm">Lade…</p>
        ) : (
          <div className="bg-gray-800 rounded-lg p-4 space-y-4">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Gitea URL (intern)</label>
              <input
                className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500"
                value={config.url}
                onChange={e => setConfig(c => ({ ...c, url: e.target.value }))}
              />
              <p className="text-xs text-gray-500 mt-1">{t("pageDesc.giteaUrl")}</p>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">API-Token</label>
              <input
                className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500 font-mono"
                type="password"
                value={config.token}
                onChange={e => setConfig(c => ({ ...c, token: e.target.value }))}
                placeholder="Gitea API-Token"
              />
              <p className="text-xs text-gray-500 mt-1">{t("pageDesc.giteaToken")}</p>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Organisation / User</label>
              <input
                className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500"
                value={config.org}
                onChange={e => setConfig(c => ({ ...c, org: e.target.value }))}
              />
              <p className="text-xs text-gray-500 mt-1">{t("pageDesc.giteaOrg")}</p>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Webhook-Secret (optional)</label>
              <input
                className="w-full bg-gray-700 text-white rounded px-3 py-2 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500 font-mono"
                type="password"
                value={config.webhook_secret}
                onChange={e => setConfig(c => ({ ...c, webhook_secret: e.target.value }))}
                placeholder="Leer lassen = kein Secret"
              />
              <p className="text-xs text-gray-500 mt-1">{t("pageDesc.giteaWebhook")}</p>
            </div>

            {msg && (
              <p className={`text-sm ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.text}</p>
            )}

            <button
              onClick={save}
              disabled={saving}
              className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-4 py-2 rounded text-sm"
            >
              <Save className="w-4 h-4" />
              {saving ? t("common.saving") : t("common.save")}
            </button>
          </div>
        )}

        {/* Repo-Liste */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-300">Repositories ({repos.length})</h2>
            <button
              onClick={load}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-white"
            >
              <RefreshCw className="w-3 h-3" /> {t("common.refresh")}
            </button>
          </div>
          {repos.length === 0 ? (
            <p className="text-gray-500 text-sm">Noch keine Repos angelegt. Erstelle ein Projekt um automatisch ein Repo zu erhalten.</p>
          ) : (
            <div className="space-y-2">
              {repos.map(r => (
                <div key={r.name} className="bg-gray-800 rounded p-3 flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white font-medium">{r.name}</p>
                    {r.description && <p className="text-xs text-gray-400">{r.description}</p>}
                    <p className="text-xs text-gray-500 mt-0.5">Branch: {r.default_branch}</p>
                  </div>
                  <a
                    href={r.html_url.replace("127.0.0.1:3001", `${window.location.hostname}:3002`)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
