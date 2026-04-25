import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Cpu, GitBranch, Github, Network, Settings, Mail, Archive, ArrowRightLeft, LayoutDashboard, Map, BookOpen, CheckCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { ConfigHubPage } from "@/pages/ConfigHubPage";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import GiteaConfigPage from "@/pages/GiteaConfigPage";
import { GitHubConfigPage } from "@/pages/GitHubConfigPage";
import { VpnPage } from "@/pages/VpnPage";
import { KasConfigPage } from "@/pages/KasConfigPage";
import { BackupPage } from "@/pages/BackupPage";
import { MigrationPage } from "@/pages/MigrationPage";
import { ReposPage } from "@/pages/ReposPage";
import { ConfigMapPage } from "@/pages/ConfigMapPage";
import { useTranslation } from "react-i18next";

type TabId = "overview" | "config-map" | "llm" | "gitea" | "github" | "repos" | "vpn" | "kas" | "backup" | "migration" | "wiki";

// ── BookStack Wiki Config Tab ──────────────────────────────────────────────

function WikiConfigTab() {
  const [baseUrl, setBaseUrl] = useState("");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<Record<string, string>>("/admin/wiki/config")
      .then(d => {
        setBaseUrl(d.base_url || "");
        setTokenId(d.token_id || "");
        setTokenSecret(d.token_secret ? "••••••••" : "");
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setSaving(true); setError(""); setSaved(false);
    try {
      const body: Record<string, string> = { base_url: baseUrl.trim() };
      if (tokenId.trim()) body.token_id = tokenId.trim();
      if (tokenSecret.trim() && !tokenSecret.startsWith("••")) body.token_secret = tokenSecret.trim();
      await api.put("/admin/wiki/config", body);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="p-6 flex justify-center"><Loader2 className="animate-spin" /></div>;

  return (
    <div className="p-6 max-w-xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">BookStack Wiki</h2>
        <p className="text-sm text-muted-foreground">
          Verbinde BookStack als Knowledge Base für alle Agenten.
          API-Token in BookStack unter <strong>Profil → API Tokens</strong> erstellen.
        </p>
      </div>

      <div className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">BookStack URL</label>
          <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
            placeholder="http://127.0.0.1:8500"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          <p className="text-xs text-muted-foreground">Lokale oder externe URL der BookStack-Instanz</p>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">Token ID</label>
          <input value={tokenId} onChange={e => setTokenId(e.target.value)}
            placeholder="z.B. 1"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">Token Secret</label>
          <input value={tokenSecret} onChange={e => setTokenSecret(e.target.value)}
            placeholder="API Token Secret"
            type="password"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring" />
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving || !baseUrl.trim()}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <CheckCircle className="h-4 w-4" /> : null}
          {saving ? "Speichere..." : saved ? "Gespeichert!" : "Speichern"}
        </button>
        {baseUrl && (
          <a href={baseUrl} target="_blank" rel="noopener noreferrer"
            className="text-xs text-primary hover:underline">
            BookStack öffnen →
          </a>
        )}
      </div>
    </div>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  // #331: Tab immer direkt aus URL ableiten — kein eigener State
  const rawTab = searchParams.get("tab") as TabId | null;
  const active: TabId = rawTab && ["overview","config-map","llm","gitea","github","repos","vpn","kas","backup","migration","wiki"].includes(rawTab)
    ? rawTab : "overview";
  const setActive = useCallback((id: TabId) => {
    setSearchParams({ tab: id }, { replace: true });
  }, [setSearchParams]);

  const TABS: { id: TabId; label: string; icon: React.ElementType; component: React.ComponentType }[] = useMemo(() => [
    { id: "overview",    label: t("settings.tabOverview", { defaultValue: "Übersicht" }), icon: LayoutDashboard,  component: ConfigHubPage },
    { id: "config-map", label: "Config Map",                                              icon: Map,              component: ConfigMapPage },
    { id: "llm",        label: t("settings.tabLlm", { defaultValue: "LLM" }),            icon: Cpu,              component: LlmConfigPage },
    { id: "gitea",     label: t("settings.tabGitea", { defaultValue: "Gitea" }),         icon: GitBranch,        component: GiteaConfigPage },
    { id: "github",    label: t("settings.tabGithub", { defaultValue: "GitHub" }),       icon: Github,           component: GitHubConfigPage },
    { id: "repos",     label: "Repos",                                                    icon: GitBranch,        component: ReposPage },
    { id: "vpn",       label: t("settings.tabVpn", { defaultValue: "VPN" }),             icon: Network,          component: VpnPage },
    { id: "kas",       label: t("settings.tabKas"),                                       icon: Mail,             component: KasConfigPage },
    { id: "backup",    label: t("settings.tabBackup", { defaultValue: "Backup" }),       icon: Archive,          component: BackupPage },
    { id: "migration", label: t("settings.tabMigration", { defaultValue: "Migration" }), icon: ArrowRightLeft,   component: MigrationPage },
    { id: "wiki",      label: "Wiki",                                                      icon: BookOpen,         component: WikiConfigTab },
  ], [t]);

  const ActiveComponent = TABS.find(tab => tab.id === active)!.component;

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <Settings size={20} className="text-muted-foreground" />
          <h1 className="text-lg font-semibold text-foreground">{t("settings.title")}</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4">{t("pageDesc.settings")}</p>
        <div className="mb-4 rounded-xl border bg-muted/30 p-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("settings.infoTitle")}</h3>
          <p className="text-xs text-muted-foreground leading-relaxed">{t("settings.infoText")}</p>
        </div>
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActive(tab.id)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px",
                active === tab.id
                  ? "border-primary text-foreground bg-background"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <tab.icon size={14} />
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        <ActiveComponent />
      </div>
    </div>
  );
}
