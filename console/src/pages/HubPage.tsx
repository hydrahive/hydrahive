import { useCallback, useEffect, useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Search, Download, CheckCircle2, ExternalLink, X, ChevronRight, RefreshCw, Package, Zap, Puzzle, Trash2, Code2, Blocks } from "lucide-react";
import { api, type HubPackage, type HubInstalledEntry, type ClawhubSkillItem, type ClawhubPackageItem } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ExtensionsPage } from "@/pages/ExtensionsPage";
import { PluginsPage } from "@/pages/PluginsPage";
import { SkillPackagesPage } from "@/pages/SkillPackagesPage";

const CATEGORY_LABELS: Record<string, string> = {
  engineering:  "Engineering",
  design:       "Design",
  marketing:    "Marketing",
  sales:        "Sales",
  product:      "Product",
  management:   "Management",
  testing:      "Testing",
  support:      "Support",
  gamedev:      "Game Dev",
  personal:     "Personal",
};

const CATEGORY_ORDER = [
  "engineering", "design", "testing", "product", "management",
  "support", "sales", "marketing", "gamedev", "personal",
];

// ── ClawhHub Tab ─────────────────────────────────────────────────────────────

function ClawhubTab() {
  const { t } = useTranslation();
  const [tab, setTab]             = useState<"skills">("skills");
  const [query, setQuery]         = useState("");
  const [skills, setSkills]       = useState<ClawhubSkillItem[]>([]);
  const [packages, setPackages]   = useState<ClawhubPackageItem[]>([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string|null>(null);
  const [pkgFamily, setPkgFamily] = useState("code-plugin");
  const [agents, setAgents]       = useState<string[]>([]);
  const [cliInstalled, setCliInstalled]   = useState<boolean|null>(null);
  const [cliInstalling, setCliInstalling] = useState(false);
  const [cliInstallLog, setCliInstallLog] = useState<string|null>(null);
  const [tokenConfigured, setTokenConfigured] = useState<boolean|null>(null);
  const [tokenPreview, setTokenPreview]       = useState<string|null>(null);
  const [tokenInput, setTokenInput]           = useState("");
  const [savingToken, setSavingToken]         = useState(false);
  const [tokenError, setTokenError]           = useState<string|null>(null);
  const [showTokenEdit, setShowTokenEdit]     = useState(false);

  // Install drawer state
  const [installTarget, setInstallTarget] = useState<ClawhubSkillItem|null>(null);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [installing, setInstalling]       = useState(false);
  const [installResult, setInstallResult] = useState<string|null>(null);
  const [installErr, setInstallErr]       = useState<string|null>(null);
  const [forceInstall, setForceInstall]   = useState(false);

  // Load agents + clawhub status
  useEffect(() => {
    // /agents gibt Dict {agentId: {...}} zurück
    api.get<Record<string, unknown>>("/agents").then(d => {
      setAgents(Object.keys(d).sort());
    }).catch(e => console.error("Failed to load agents for hub", e));
    api.clawhubStatus().then(d => {
      setCliInstalled(d.installed);
      setTokenConfigured(d.token_configured);
      setTokenPreview(d.token_preview);
    }).catch(() => setCliInstalled(false));
  }, []);

  async function installCli() {
    setCliInstalling(true); setCliInstallLog(null);
    try {
      const r = await api.clawhubInstallCli();
      setCliInstalled(true);
      setCliInstallLog(r.output || "OK");
    } catch (e: any) {
      setCliInstallLog("Fehler: " + e.message);
    } finally { setCliInstalling(false); }
  }

  async function searchSkills(q: string) {
    setLoading(true); setError(null);
    try {
      const d = await api.clawhubSkills(q);
      setSkills(d.items);
    } catch (e: any) {
      setError(e.message);
    } finally { setLoading(false); }
  }

  async function loadPackages(family: string) {
    setLoading(true); setError(null);
    try {
      const d = await api.clawhubPackages(family);
      setPackages(d.items);
    } catch (e: any) {
      setError(e.message);
    } finally { setLoading(false); }
  }

  useEffect(() => {
    if (tab === "skills") searchSkills(query);
    else loadPackages(pkgFamily);
  }, [tab, pkgFamily]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    searchSkills(query);
  }

  function openInstall(skill: ClawhubSkillItem) {
    setInstallTarget(skill);
    setInstallResult(null);
    setInstallErr(null);
    setForceInstall(false);
    setSelectedAgent(agents[0] || "");
  }

  async function doInstall() {
    if (!installTarget || !selectedAgent) return;
    setInstalling(true); setInstallErr(null); setInstallResult(null);
    try {
      const r = await api.clawhubInstallSkill(installTarget.slug, selectedAgent, forceInstall);
      setInstallResult(`Skill "${r.skill_name}" → ${r.file} installiert`);
    } catch (e: any) {
      const msg = e.message || "Fehler";
      if (msg.includes("verdächtig") || msg.includes("suspicious")) {
        setInstallErr(msg);
        setForceInstall(true);
      } else {
        setInstallErr(msg);
      }
    } finally { setInstalling(false); }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tabs */}
      {/* ClawhHub Skills */}

      {/* clawhub CLI nicht installiert Banner */}
      {cliInstalled === false && (
        <div className="mx-4 mt-4 rounded-xl border border-orange-500/40 bg-orange-500/10 p-4 flex items-center justify-between gap-4 flex-shrink-0">
          <div>
            <p className="text-sm font-medium text-orange-600">clawhub CLI nicht installiert</p>
            <p className="text-xs text-muted-foreground mt-0.5">Wird für Skills & Plugins benötigt</p>
          </div>
          <button
            onClick={installCli}
            disabled={cliInstalling}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-orange-500 text-white text-sm font-medium hover:bg-orange-600 transition-colors disabled:opacity-50 flex-shrink-0"
          >
            {cliInstalling ? <><RefreshCw className="h-3.5 w-3.5 animate-spin" />Installiere…</> : <><Download className="h-3.5 w-3.5" />Jetzt installieren</>}
          </button>
        </div>
      )}
      {cliInstallLog && (
        <div className={`mx-4 mt-2 rounded-lg p-3 text-xs font-mono flex-shrink-0 ${cliInstalled ? "bg-green-500/10 text-green-600 border border-green-500/30" : "bg-destructive/10 text-destructive border border-destructive/30"}`}>
          {cliInstallLog}
        </div>
      )}

      {/* ClawhHub Token */}
      {tokenConfigured === false && cliInstalled !== false && (
        <div className="mx-4 mt-4 rounded-xl border border-blue-500/30 bg-blue-500/5 p-4 space-y-3 flex-shrink-0">
          <p className="text-sm font-medium text-blue-600">ClawhHub API Token</p>
          <p className="text-xs text-muted-foreground">
            Token wird für die Skill-Suche und Installation benötigt. Erstelle einen unter{" "}
            <a href="https://clawhub.ai/settings" target="_blank" rel="noopener" className="underline text-blue-500">clawhub.ai/settings</a>.
          </p>
          <div className="flex gap-2">
            <input type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)}
              placeholder="clh_..."
              className="flex-1 px-3 py-2 rounded-lg border border-border/50 bg-background text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/30" />
            <button
              onClick={async () => {
                setSavingToken(true); setTokenError(null);
                try {
                  const r = await api.clawhubSetToken(tokenInput.trim());
                  setTokenConfigured(true); setTokenPreview(r.token_preview); setTokenInput("");
                } catch (e: any) { setTokenError(e.message); }
                finally { setSavingToken(false); }
              }}
              disabled={savingToken || !tokenInput.trim()}
              className="flex items-center gap-1.5 px-4 py-2 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors disabled:opacity-50">
              {savingToken ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : "Speichern"}
            </button>
          </div>
          {tokenError && <p className="text-xs text-destructive">{tokenError}</p>}
        </div>
      )}
      {tokenConfigured && (
        <div className="mx-4 mt-3 flex items-center gap-2 text-xs text-muted-foreground flex-shrink-0">
          <span>Token: <code className="bg-muted px-1.5 py-0.5 rounded">{tokenPreview}</code></span>
          <button onClick={() => setShowTokenEdit(e => !e)} className="underline hover:text-foreground">ändern</button>
          {showTokenEdit && (
            <div className="flex gap-2 ml-2">
              <input type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)}
                placeholder="clh_..."
                className="px-2 py-1 rounded border border-border/50 bg-background text-xs font-mono w-48 focus:outline-none focus:ring-1 focus:ring-blue-500/30" />
              <button
                onClick={async () => {
                  setSavingToken(true); setTokenError(null);
                  try {
                    const r = await api.clawhubSetToken(tokenInput.trim());
                    setTokenConfigured(true); setTokenPreview(r.token_preview); setTokenInput(""); setShowTokenEdit(false);
                  } catch (e: any) { setTokenError(e.message); }
                  finally { setSavingToken(false); }
                }}
                disabled={savingToken || !tokenInput.trim()}
                className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">
                OK
              </button>
            </div>
          )}
        </div>
      )}

      {/* Skills Tab */}
      {tab === "skills" && (
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden p-4 gap-4">
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="ClawhHub Skills suchen…"
              className="w-full pl-10 pr-20 py-2.5 rounded-xl border border-border/50 bg-background/50 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <button
              type="submit"
              disabled={loading}
              className="absolute right-2 top-1/2 -translate-y-1/2 px-3 py-1 rounded-lg bg-primary text-primary-foreground text-xs disabled:opacity-50"
            >
              {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : "Suchen"}
            </button>
          </form>

          {error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
          )}

          <div className="flex-1 overflow-y-auto">
            {skills.length === 0 && !loading ? (
              <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
                Keine Skills gefunden
              </div>
            ) : (
              <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {skills.map(skill => (
                  <button
                    key={skill.slug}
                    onClick={() => openInstall(skill)}
                    className="text-left rounded-2xl border border-border/50 bg-card/80 p-4 hover:border-primary/40 hover:shadow-md transition-all group"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <Zap className="h-5 w-5 text-amber-500 flex-shrink-0" />
                      {skill.score !== null && (
                        <span className="text-xs text-muted-foreground">{skill.score.toFixed(2)}</span>
                      )}
                    </div>
                    <div className="font-medium text-sm leading-tight mb-1 line-clamp-1">{skill.name}</div>
                    <div className="text-xs text-muted-foreground font-mono line-clamp-1 mb-3">{skill.slug}</div>
                    <div className="flex items-center justify-between">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600">ClawhHub Skill</span>
                      <ChevronRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Install Drawer */}
      {installTarget && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={() => setInstallTarget(null)} />
          <div className="w-full max-w-md bg-background border-l border-border/50 flex flex-col overflow-hidden shadow-2xl">
            <div className="flex items-start justify-between p-6 border-b border-border/40 flex-shrink-0">
              <div className="flex items-center gap-3">
                <Zap className="h-6 w-6 text-amber-500" />
                <div>
                  <h2 className="text-lg font-semibold">{installTarget.name}</h2>
                  <p className="text-xs text-muted-foreground font-mono">{installTarget.slug}</p>
                </div>
              </div>
              <button onClick={() => setInstallTarget(null)} className="p-1.5 rounded-lg hover:bg-muted/50">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              <p className="text-sm text-muted-foreground">
                Skill aus ClawhHub importieren und in den Skills-Ordner eines Agenten installieren.
              </p>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Ziel-Agent</label>
                <select
                  value={selectedAgent}
                  onChange={e => setSelectedAgent(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                >
                  {agents.length === 0 && <option value="">– kein Agent verfügbar –</option>}
                  {agents.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>

              {forceInstall && (
                <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 p-3 space-y-2">
                  <p className="text-xs text-orange-600 font-medium">Skill ist als potenziell unsicher markiert.</p>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      checked={forceInstall}
                      onChange={e => setForceInstall(e.target.checked)}
                    />
                    Ich habe den Skill geprüft und möchte trotzdem installieren
                  </label>
                </div>
              )}

              {installResult && (
                <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-3 flex items-center gap-2 text-sm text-green-600">
                  <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                  {installResult}
                </div>
              )}
              {installErr && !forceInstall && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                  {installErr}
                </div>
              )}
            </div>

            <div className="p-6 border-t border-border/40 flex-shrink-0">
              {installResult ? (
                <button
                  onClick={() => setInstallTarget(null)}
                  className="w-full py-2.5 rounded-xl border border-border/50 text-sm hover:bg-muted/50 transition-colors"
                >
                  Schließen
                </button>
              ) : (
                <button
                  onClick={doInstall}
                  disabled={installing || !selectedAgent}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-amber-500 text-white text-sm font-medium hover:bg-amber-600 transition-colors disabled:opacity-50"
                >
                  {installing ? (
                    <><RefreshCw className="h-4 w-4 animate-spin" />Installiere…</>
                  ) : (
                    <><Download className="h-4 w-4" />In Agent installieren</>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── HydraHub Tab ──────────────────────────────────────────────────────────────

function HydraHubTab() {
  const { t } = useTranslation();
  const [packages, setPackages]     = useState<HubPackage[]>([]);
  const [installed, setInstalled]   = useState<HubInstalledEntry[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [query, setQuery]           = useState("");
  const [category, setCategory]     = useState("all");
  const [selected, setSelected]     = useState<HubPackage | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installErr, setInstallErr] = useState<string | null>(null);
  const [agentIdInput, setAgentIdInput] = useState("");
  const [hubUpdated, setHubUpdated] = useState("");
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [idx, inst] = await Promise.all([api.hubIndex(), api.hubInstalled()]);
      setPackages(idx.packages);
      setInstalled(Array.isArray(inst) ? inst : []);
      setHubUpdated(idx.updated);
    } catch (e: any) {
      setError(e.message || t("hub.notReachable"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const installedIds = useMemo(
    () => new Set(installed.map((i) => i.id)),
    [installed]
  );

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return packages.filter((p) => {
      if (category !== "all" && p.category !== category) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q) ||
        p.tags.some((tag) => tag.toLowerCase().includes(q))
      );
    });
  }, [packages, query, category]);

  const categories = useMemo(() => {
    const counts: Record<string, number> = {};
    packages.forEach((p) => {
      counts[p.category] = (counts[p.category] || 0) + 1;
    });
    return CATEGORY_ORDER.filter((c) => counts[c] > 0).map((c) => ({
      key: c,
      label: CATEGORY_LABELS[c] ?? c,
      count: counts[c],
    }));
  }, [packages]);

  async function install(pkg: HubPackage) {
    setInstalling(pkg.id);
    setInstallErr(null);
    const agentId = agentIdInput.trim() || undefined;
    try {
      await api.hubInstall({ id: pkg.id, agent_id_override: agentId });
      const inst = await api.hubInstalled();
      setInstalled(Array.isArray(inst) ? inst : []);
      setSelected(null);
      setAgentIdInput("");
    } catch (e: any) {
      setInstallErr(e.message || "Installation fehlgeschlagen");
    } finally {
      setInstalling(null);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Header ── */}
      <div className="px-4 pt-4 pb-4 border-b border-border/40 flex-shrink-0">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-muted-foreground">
            {packages.length > 0
              ? t("hub.packagesAvailable", { count: packages.length })
              : t("hub.subtitle")}
            {hubUpdated && (
              <span className="ml-2 opacity-50 text-xs">
                · Stand: {new Date(hubUpdated).toLocaleDateString("de-DE")}
              </span>
            )}
          </p>
          <button
            onClick={load}
            disabled={loading}
            className="p-2 rounded-xl border border-border/50 hover:bg-muted/50 transition-colors disabled:opacity-40"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* Suche */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("hub.searchPlaceholder")}
            className="w-full pl-10 pr-4 py-2.5 rounded-xl border border-border/50 bg-background/50 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* ── Kategorien-Sidebar ── */}
        <div className="w-44 flex-shrink-0 border-r border-border/40 overflow-y-auto py-3 px-2">
          <button
            onClick={() => setCategory("all")}
            className={`w-full text-left px-3 py-1.5 rounded-lg text-sm flex items-center justify-between transition-colors
              ${category === "all" ? "bg-primary text-primary-foreground" : "hover:bg-muted/50 text-muted-foreground hover:text-foreground"}`}
          >
            <span>{t("hub.all")}</span>
            <span className="text-xs opacity-70">{packages.length}</span>
          </button>
          {categories.map(({ key, label, count }) => (
            <button
              key={key}
              onClick={() => setCategory(key)}
              className={`w-full text-left px-3 py-1.5 rounded-lg text-sm flex items-center justify-between transition-colors mt-0.5
                ${category === key ? "bg-primary text-primary-foreground" : "hover:bg-muted/50 text-muted-foreground hover:text-foreground"}`}
            >
              <span>{label}</span>
              <span className="text-xs opacity-70">{count}</span>
            </button>
          ))}
        </div>

        {/* ── Pakete-Grid ── */}
        <div className="flex-1 overflow-y-auto p-4">
          {error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive mb-4">
              {error}
            </div>
          )}

          {loading && packages.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              Lade Hub-Index...
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              Keine Pakete gefunden
            </div>
          ) : (
            <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filtered.map((pkg) => {
                const isInstalled = installedIds.has(pkg.id);
                return (
                  <button
                    key={pkg.id}
                    onClick={() => { setSelected(pkg); setInstallErr(null); setAgentIdInput(""); }}
                    className="text-left rounded-2xl border border-border/50 bg-card/80 p-4 hover:border-primary/40 hover:shadow-md transition-all group"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <span className="text-2xl leading-none">{pkg.icon}</span>
                      {isInstalled && (
                        <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
                      )}
                    </div>
                    <div className="font-medium text-sm leading-tight mb-1 line-clamp-1">
                      {pkg.name}
                    </div>
                    <div className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2.5rem]">
                      {pkg.description}
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-muted/60 text-muted-foreground">
                        {CATEGORY_LABELS[pkg.category] ?? pkg.category}
                      </span>
                      <ChevronRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Detail-Drawer ── */}
      {selected && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={() => setSelected(null)} />
          <div className="w-full max-w-lg bg-background border-l border-border/50 flex flex-col overflow-hidden shadow-2xl">
            {/* Drawer Header */}
            <div className="flex items-start justify-between p-6 border-b border-border/40 flex-shrink-0">
              <div className="flex items-center gap-3">
                <span className="text-3xl">{selected.icon}</span>
                <div>
                  <h2 className="text-lg font-semibold">{selected.name}</h2>
                  <p className="text-xs text-muted-foreground">
                    {CATEGORY_LABELS[selected.category] ?? selected.category}
                    {selected.author && ` · von ${selected.author}`}
                    {selected.version && ` · v${selected.version}`}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="p-1.5 rounded-lg hover:bg-muted/50 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Drawer Content */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              <p className="text-sm text-muted-foreground">{selected.description}</p>

              {selected.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {selected.tags.map((tag) => (
                    <span key={tag} className="text-xs px-2 py-0.5 rounded-full bg-muted/60 text-muted-foreground">
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {selected.source_url && (
                <a
                  href={selected.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  Quelle ansehen
                </a>
              )}

              {/* Agent-ID Override */}
              {!installedIds.has(selected.id) && (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Agent-ID (optional — Standard: {selected.id})
                  </label>
                  <input
                    value={agentIdInput}
                    onChange={(e) => setAgentIdInput(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                    placeholder={selected.id}
                    className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background/50 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/30"
                  />
                </div>
              )}

              {installErr && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                  {installErr}
                </div>
              )}
            </div>

            {/* Drawer Footer */}
            <div className="p-6 border-t border-border/40 flex-shrink-0">
              {installedIds.has(selected.id) ? (
                <button
                  onClick={() => {
                    setConfirmState({
                      title: t("confirm.titleUninstall"),
                      message: t("confirm.uninstallAgent", { name: selected.id }),
                      action: async () => {
                        setInstalling(selected.id);
                        try {
                          await api.hubUninstall(selected.id);
                          const inst = await api.hubInstalled();
                          setInstalled(Array.isArray(inst) ? inst : []);
                          setSelected(null);
                        } catch (e: any) { setInstallErr(e.message); }
                        finally { setInstalling(null); }
                      },
                    });
                  }}
                  disabled={installing === selected.id}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-destructive/50 text-destructive text-sm font-medium hover:bg-destructive/10 transition-colors disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" /> Deinstallieren
                </button>
              ) : (
                <button
                  onClick={() => install(selected)}
                  disabled={installing === selected.id}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  {installing === selected.id ? (
                    <>
                      <RefreshCw className="h-4 w-4 animate-spin" />
                      Installiere...
                    </>
                  ) : (
                    <>
                      <Download className="h-4 w-4" />
                      Installieren
                    </>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
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

// ── HydraHub Plugins Tab ──────────────────────────────────────────────────────

function HubPluginsTab() {
  const { t } = useTranslation();
  const [plugins, setPlugins]       = useState<HubPackage[]>([]);
  const [installed, setInstalled]   = useState<Set<string>>(new Set());
  const [loading, setLoading]       = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [error, setError]           = useState<string | null>(null);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  const [localPlugins, setLocalPlugins] = useState<HubPackage[]>([]);

  async function load() {
    setLoading(true); setError(null);
    try {
      const [idx, inst, local] = await Promise.all([
        api.hubIndex().catch(() => ({ packages: [] })),
        api.hubInstalled().catch(() => []),
        api.hubLocalPlugins().catch(() => ({ plugins: [] })),
      ]);
      setPlugins(idx.packages.filter((p: any) => p.type === "plugin"));
      const instArr = Array.isArray(inst) ? inst : [];
      setInstalled(new Set(instArr.map((i: any) => i.id)));
      setLocalPlugins((local.plugins || []).map((p: any) => ({
        ...p, path: `local/${p.id}`, installed: true,
      })));
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function installPlugin(pkg: HubPackage) {
    setInstalling(pkg.id); setError(null);
    try {
      await api.hubInstall({ id: pkg.id });
      const inst = await api.hubInstalled();
      setInstalled(new Set((Array.isArray(inst) ? inst : []).map((i: any) => i.id)));
    } catch (e: any) { setError(e.message); }
    finally { setInstalling(null); }
  }

  function uninstallPlugin(id: string) {
    setConfirmState({
      title: t("confirm.titleUninstall"),
      message: t("confirm.uninstallPlugin", { name: id }),
      action: async () => {
        setInstalling(id); setError(null);
        try {
          await api.hubUninstallPlugin(id);
          const inst = await api.hubInstalled();
          setInstalled(new Set((Array.isArray(inst) ? inst : []).map((i: any) => i.id)));
        } catch (e: any) { setError(e.message); }
        finally { setInstalling(null); }
      },
    });
  }

  return (
    <div className="flex flex-col h-full overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between flex-shrink-0">
        <p className="text-sm text-muted-foreground">
          {plugins.length > 0 ? t("hub.pluginsAvailable", { count: plugins.length }) : t("hub.noPlugins")}
        </p>
        <button onClick={load} disabled={loading} className="p-2 rounded-xl border border-border/50 hover:bg-muted/50 disabled:opacity-40">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
      )}

      <div className="flex-1 overflow-y-auto">
        {loading && plugins.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Lade...</div>
        ) : plugins.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Noch keine Plugins im Hub</div>
        ) : (
          <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {plugins.map(pkg => {
              const isInstalled = installed.has(pkg.id);
              return (
                <div key={pkg.id} className="rounded-2xl border border-border/50 bg-card/80 p-4">
                  <div className="flex items-start justify-between mb-2">
                    <span className="text-2xl leading-none">{pkg.icon}</span>
                    {isInstalled && <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />}
                  </div>
                  <div className="font-medium text-sm mb-1">{pkg.name}</div>
                  <div className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2.5rem]">{pkg.description}</div>
                  <div className="flex flex-wrap gap-1 mb-3">
                    {pkg.tags.map(t => (
                      <span key={t} className="text-xs px-1.5 py-0.5 rounded-full bg-muted/60 text-muted-foreground">{t}</span>
                    ))}
                  </div>
                  {isInstalled ? (
                    <button
                      onClick={() => uninstallPlugin(pkg.id)}
                      disabled={installing === pkg.id}
                      className="w-full flex items-center justify-center gap-2 py-2 rounded-xl border border-destructive/50 text-destructive text-xs font-medium hover:bg-destructive/10 transition-colors disabled:opacity-50"
                    >
                      {installing === pkg.id
                        ? <><RefreshCw className="h-3.5 w-3.5 animate-spin" />Entferne...</>
                        : <><Trash2 className="h-3.5 w-3.5" />Deinstallieren</>
                      }
                    </button>
                  ) : (
                    <button
                      onClick={() => installPlugin(pkg)}
                      disabled={installing === pkg.id}
                      className="w-full flex items-center justify-center gap-2 py-2 rounded-xl bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                      {installing === pkg.id
                        ? <><RefreshCw className="h-3.5 w-3.5 animate-spin" />Installiere...</>
                        : <><Download className="h-3.5 w-3.5" />Installieren</>
                      }
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {/* Lokale Plugins (#262) */}
        {localPlugins.length > 0 && (
          <>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mt-6 mb-2 px-1">
              {t("hub.localPlugins", { defaultValue: "Lokale Plugins" })} ({localPlugins.length})
            </h3>
            <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {localPlugins.map((p: any) => (
                <div key={p.id} className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
                  <div className="flex items-start justify-between mb-2">
                    <span className="text-2xl leading-none">🔌</span>
                    <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
                  </div>
                  <div className="font-medium text-sm mb-1">{p.name}</div>
                  <div className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2.5rem]">{p.description}</div>
                  <div className="text-[10px] text-muted-foreground">v{p.version} · {p.author}</div>
                </div>
              ))}
            </div>
          </>
        )}
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

// ── Haupt-Komponente ──────────────────────────────────────────────────────────

type HubTabId = "hydrahub" | "hub-plugins" | "clawhub" | "extensions" | "plugins" | "skill-packages";

const VALID_HUB_TABS: HubTabId[] = ["hydrahub", "hub-plugins", "clawhub", "extensions", "plugins", "skill-packages"];

export function HubPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  // #331: Tab immer direkt aus URL
  const rawTab = searchParams.get("tab") as HubTabId | null;
  const activeTab: HubTabId = rawTab && VALID_HUB_TABS.includes(rawTab) ? rawTab : "hydrahub";
  const setActiveTab = useCallback((id: HubTabId) => {
    setSearchParams({ tab: id }, { replace: true });
  }, [setSearchParams]);

  const tabCls = (id: string) => `flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px
    ${activeTab === id
      ? "border-primary text-foreground bg-background"
      : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"}`;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 pt-6 pb-0 border-b border-border flex-shrink-0">
        <h1 className="text-2xl font-bold tracking-tight mb-1">{t("nav.hydraHub")}</h1>
        <p className="text-xs text-muted-foreground mb-4">{t("pageDesc.hub", { defaultValue: "Pakete, Extensions, Plugins und Skills verwalten" })}</p>
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          <button onClick={() => setActiveTab("hydrahub")} className={tabCls("hydrahub")}>
            <Package size={14} />
            {t("hub.tabAgents")}
          </button>
          <button onClick={() => setActiveTab("hub-plugins")} className={tabCls("hub-plugins")}>
            <Puzzle size={14} />
            {t("hub.tabHubPlugins")}
          </button>
          <button onClick={() => setActiveTab("clawhub")} className={tabCls("clawhub")}>
            <Zap size={14} />
            {t("hub.tabClawhub")}
          </button>
          <button onClick={() => setActiveTab("extensions")} className={tabCls("extensions")}>
            <Code2 size={14} />
            {t("hub.tabExtensions")}
          </button>
          <button onClick={() => setActiveTab("plugins")} className={tabCls("plugins")}>
            <Blocks size={14} />
            {t("hub.tabPlugins")}
          </button>
          <button onClick={() => setActiveTab("skill-packages")} className={tabCls("skill-packages")}>
            <Package size={14} />
            {t("hub.tabSkillPackages")}
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeTab === "hydrahub" && <HydraHubTab />}
        {activeTab === "hub-plugins" && <HubPluginsTab />}
        {activeTab === "clawhub" && <ClawhubTab />}
        {activeTab === "extensions" && <ExtensionsPage />}
        {activeTab === "plugins" && <PluginsPage />}
        {activeTab === "skill-packages" && <SkillPackagesPage />}
      </div>
    </div>
  );
}
