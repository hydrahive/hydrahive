import { useEffect, useState, useMemo } from "react";
import { Search, Download, CheckCircle2, ExternalLink, X, ChevronRight, RefreshCw, Package, Zap, Puzzle, Trash2 } from "lucide-react";
import { api, type HubPackage, type HubInstalledEntry, type ClawhubSkillItem, type ClawhubPackageItem } from "@/lib/api";
import { useTranslation } from "react-i18next";

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
  const [tab, setTab]             = useState<"skills"|"plugins">("skills");
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
    }).catch(() => {});
    api.clawhubStatus().then(d => setCliInstalled(d.installed)).catch(() => setCliInstalled(false));
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
      <div className="flex gap-1 px-4 pt-4 border-b border-border/40 flex-shrink-0">
        <button
          onClick={() => setTab("skills")}
          className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors
            ${tab === "skills" ? "bg-background border border-b-background border-border/50 -mb-px text-foreground" : "text-muted-foreground hover:text-foreground"}`}
        >
          Skills
        </button>
        <button
          onClick={() => setTab("plugins")}
          className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors
            ${tab === "plugins" ? "bg-background border border-b-background border-border/50 -mb-px text-foreground" : "text-muted-foreground hover:text-foreground"}`}
        >
          Plugins
        </button>
      </div>

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

      {/* Plugins Tab */}
      {tab === "plugins" && (
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden p-4 gap-4">
          <div className="flex gap-2 flex-shrink-0">
            {[
              { key: "code-plugin", label: "Code Plugins" },
              { key: "bundle-plugin", label: "Bundle Plugins" },
              { key: "skill", label: "Skill Packages" },
            ].map(f => (
              <button
                key={f.key}
                onClick={() => setPkgFamily(f.key)}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors
                  ${pkgFamily === f.key ? "bg-primary text-primary-foreground" : "border border-border/50 hover:bg-muted/50 text-muted-foreground"}`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
          )}
          {loading && (
            <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
              <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Lade…
            </div>
          )}

          <div className="flex-1 overflow-y-auto">
            {!loading && packages.length === 0 ? (
              <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Keine Packages gefunden</div>
            ) : (
              <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {packages.map(pkg => (
                  <div
                    key={pkg.name}
                    className="text-left rounded-2xl border border-border/50 bg-card/80 p-4"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <Package className="h-5 w-5 text-blue-500 flex-shrink-0" />
                      <span className="text-xs text-muted-foreground">v{pkg.latestVersion}</span>
                    </div>
                    <div className="font-medium text-sm leading-tight mb-1 line-clamp-1">{pkg.displayName}</div>
                    <div className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2.5rem]">{pkg.summary}</div>
                    <div className="flex items-center justify-between">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-600">
                        {pkg.family === "code-plugin" ? "Code Plugin" : pkg.family === "bundle-plugin" ? "Bundle Plugin" : "Skill"}
                      </span>
                      {pkg.executesCode && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-600">Führt Code aus</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-2 opacity-60">by @{pkg.ownerHandle}</div>
                  </div>
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

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [idx, inst] = await Promise.all([api.hubIndex(), api.hubInstalled()]);
      setPackages(idx.packages);
      setInstalled(Array.isArray(inst) ? inst : []);
      setHubUpdated(idx.updated);
    } catch (e: any) {
      setError(e.message || "Hub nicht erreichbar");
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
              ? `${packages.length} Pakete verfügbar`
              : "Kuratierte Agenten, Erweiterungen und Tools"}
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
            placeholder="Suche nach Name, Beschreibung oder Tag..."
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
            <span>Alle</span>
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
                  onClick={async () => {
                    if (!confirm(`Agent "${selected.id}" deinstallieren?`)) return;
                    setInstalling(selected.id);
                    try {
                      await api.hubUninstall(selected.id);
                      const inst = await api.hubInstalled();
                      setInstalled(Array.isArray(inst) ? inst : []);
                      setSelected(null);
                    } catch (e: any) { setInstallErr(e.message); }
                    finally { setInstalling(null); }
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
    </div>
  );
}

// ── HydraHub Plugins Tab ──────────────────────────────────────────────────────

function HubPluginsTab() {
  const [plugins, setPlugins]       = useState<HubPackage[]>([]);
  const [installed, setInstalled]   = useState<Set<string>>(new Set());
  const [loading, setLoading]       = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [error, setError]           = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try {
      const [idx, inst] = await Promise.all([api.hubIndex(), api.hubInstalled()]);
      setPlugins(idx.packages.filter((p: any) => p.type === "plugin"));
      const instArr = Array.isArray(inst) ? inst : [];
      setInstalled(new Set(instArr.map((i: any) => i.id)));
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

  async function uninstallPlugin(id: string) {
    if (!confirm(`Plugin "${id}" wirklich deinstallieren?`)) return;
    setInstalling(id); setError(null);
    try {
      await api.hubUninstallPlugin(id);
      const inst = await api.hubInstalled();
      setInstalled(new Set((Array.isArray(inst) ? inst : []).map((i: any) => i.id)));
    } catch (e: any) { setError(e.message); }
    finally { setInstalling(null); }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between flex-shrink-0">
        <p className="text-sm text-muted-foreground">
          {plugins.length > 0 ? `${plugins.length} Plugin(s) verfügbar` : "Keine Plugins im Hub"}
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
      </div>
    </div>
  );
}

// ── Haupt-Komponente ──────────────────────────────────────────────────────────

export function HubPage() {
  const [activeTab, setActiveTab] = useState<"hydrahub"|"plugins"|"clawhub">("hydrahub");

  const tabCls = (t: string) => `px-4 py-2 text-sm font-medium rounded-t-lg transition-colors
    ${activeTab === t
      ? "bg-background border border-b-background border-border/50 -mb-px text-foreground"
      : "text-muted-foreground hover:text-foreground"}`;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 pt-6 pb-0 border-b border-border/40 flex-shrink-0">
        <h1 className="text-2xl font-bold tracking-tight mb-4">HydraHub</h1>
        <div className="flex gap-1">
          <button onClick={() => setActiveTab("hydrahub")} className={tabCls("hydrahub")}>
            Agenten
          </button>
          <button onClick={() => setActiveTab("plugins")} className={tabCls("plugins") + " flex items-center gap-1.5"}>
            <Puzzle className="h-3.5 w-3.5 text-primary" />
            Plugins
          </button>
          <button onClick={() => setActiveTab("clawhub")} className={tabCls("clawhub") + " flex items-center gap-1.5"}>
            <Zap className="h-3.5 w-3.5 text-amber-500" />
            ClawhHub
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === "hydrahub" ? <HydraHubTab /> : activeTab === "plugins" ? <HubPluginsTab /> : <ClawhubTab />}
      </div>
    </div>
  );
}
