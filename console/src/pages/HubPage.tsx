import { useEffect, useState, useMemo } from "react";
import { Search, Download, CheckCircle2, ExternalLink, X, ChevronRight, RefreshCw } from "lucide-react";
import { api, type HubPackage, type HubInstalledEntry } from "@/lib/api";
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

export function HubPage() {
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
      <div className="px-6 pt-6 pb-4 border-b border-border/40 flex-shrink-0">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">HydraHub</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              {packages.length > 0
                ? `${packages.length} Pakete verfügbar`
                : "Kuratierte Agenten, Erweiterungen und Tools"}
              {hubUpdated && (
                <span className="ml-2 opacity-50 text-xs">
                  · Stand: {new Date(hubUpdated).toLocaleDateString("de-DE")}
                </span>
              )}
            </p>
          </div>
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
                <div className="flex items-center gap-2 text-sm text-green-600">
                  <CheckCircle2 className="h-4 w-4" />
                  Bereits installiert
                </div>
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
