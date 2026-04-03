import { useEffect, useRef, useState } from "react";
import { Search, CheckCircle, XCircle, RefreshCw, Play, ExternalLink, Download, Loader2 } from "lucide-react";
import { api, SearxngStatus, SearxngTestResult } from "@/lib/api";
import { useTranslation } from "react-i18next";

export function SearchPage() {
  const { t } = useTranslation();
  const [status,     setStatus]     = useState<SearxngStatus | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error,      setError]      = useState("");

  const [query,      setQuery]      = useState("");
  const [engines,    setEngines]    = useState<string[]>([]);
  const [searching,  setSearching]  = useState(false);
  const [result,     setResult]     = useState<SearxngTestResult | null>(null);

  async function load() {
    try {
      const d = await api.searxngStatus();
      setStatus(d);
      // Standardmäßig keine Engines vorauswählen — leere Liste = SearXNG wählt selbst
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("searchPage.loadError"));
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);

  function refresh() { setRefreshing(true); load(); }

  async function handleSearch() {
    if (!query.trim()) return;
    setSearching(true);
    setResult(null);
    try {
      const r = await api.searxngTest({ query: query.trim(), engines: engines.join(",") || undefined });
      setResult(r);
    } catch (e) {
      setResult({ results: [], error: e instanceof Error ? e.message : t("searchPage.genericError") });
    } finally { setSearching(false); }
  }

  const isRunning   = status?.service_active && status?.http_ok && status?.json_ok;
  const isInstalled = status?.installed ?? true;

  const [installing, setInstalling] = useState(false);
  const [installLog, setInstallLog] = useState<string[]>([]);
  const [installDone, setInstallDone] = useState<boolean | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  async function handleInstall() {
    setInstalling(true);
    setInstallLog([]);
    setInstallDone(null);
    const token = localStorage.getItem("hydrahive_token") || "";
    const res = await fetch("/api/admin/searxng/install", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok || !res.body) {
      setInstallLog([`Fehler: HTTP ${res.status}`]);
      setInstalling(false);
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.replace(/^data: /, "").trim();
        if (!line) continue;
        try {
          const d = JSON.parse(line);
          if (d.line !== undefined) setInstallLog(l => [...l, d.line]);
          if (d.done) { setInstallDone(d.ok); setInstalling(false); }
        } catch { /* ignore */ }
      }
    }
    setInstalling(false);
  }

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [installLog]);

  if (loading) return (
    <div className="p-8 text-sm text-muted-foreground">{t("searchPage.loading")}</div>
  );

  if (!isInstalled) return (
    <div className="p-8 max-w-xl space-y-5">
      <div className="flex items-center gap-3">
        <XCircle className="w-6 h-6 text-yellow-500 shrink-0" />
        <h2 className="text-lg font-semibold">{t("searchPage.notInstalledTitle")}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t("searchPage.notInstalledBody")}</p>

      {installDone === null && (
        <button
          onClick={handleInstall}
          disabled={installing}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium"
        >
          {installing
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : <Download className="w-4 h-4" />}
          {installing ? t("searchPage.installing") : t("searchPage.installBtn")}
        </button>
      )}

      {installLog.length > 0 && (
        <div
          ref={logRef}
          className="rounded-xl bg-zinc-950 border border-zinc-800 p-4 font-mono text-xs text-zinc-300 max-h-72 overflow-y-auto space-y-0.5"
        >
          {installLog.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
        </div>
      )}

      {installDone === true && (
        <div className="flex items-center gap-2 text-green-400 text-sm">
          <CheckCircle className="w-4 h-4" />
          {t("searchPage.installSuccess")}
          <button onClick={load} className="ml-2 underline">{t("searchPage.reload")}</button>
        </div>
      )}
      {installDone === false && (
        <div className="flex items-center gap-2 text-red-400 text-sm">
          <XCircle className="w-4 h-4" />
          {t("searchPage.installFailed")}
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-4 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className={`status-pill ${isRunning ? "status-pill-ok" : "status-pill-warn"}`}>
                {isRunning ? t("searchPage.active") : t("searchPage.inactive")}
              </span>
            </div>
            <div>
              <h1 className="shell-title">{t("searchPage.title")}</h1>
              <p className="text-xs text-muted-foreground">{t("pageDesc.search")}</p>
              <p className="shell-copy mt-2 max-w-2xl">{t("searchPage.subtitle")}</p>
            </div>
          </div>
          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">{t("searchPage.statusLabel")}</p>
                <button onClick={refresh} disabled={refreshing}
                  className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs hover:bg-accent transition-colors">
                  <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
                  {t("searchPage.refresh")}
                </button>
              </div>
              <div className="mt-3 space-y-2">
                <StatusRow label={t("searchPage.statusService")} ok={status?.service_active ?? false} />
                <StatusRow label={t("searchPage.statusHttp")}    ok={status?.http_ok       ?? false} />
                <StatusRow label={t("searchPage.statusJson")}    ok={status?.json_ok       ?? false} />
                <StatusRow label={t("searchPage.statusInstall")} ok={status?.installed     ?? false} detail="/opt/searxng" />
                <StatusRow label={t("searchPage.statusConfig")}  ok={status?.config_exists ?? false} detail="/etc/searxng/settings.yml" />
              </div>
              {status?.version && (
                <p className="mt-3 text-xs text-muted-foreground font-mono">{status.version}</p>
              )}
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>
      )}

      {!isRunning && status?.http_ok && !status?.json_ok && (
        <div className="rounded-2xl border border-orange-500/30 bg-orange-500/10 px-4 py-3 text-sm text-orange-600 dark:text-orange-400 space-y-3">
          <p>{t("searchPage.jsonNotEnabled")}</p>
          {installDone === null && (
            <button
              onClick={handleInstall}
              disabled={installing}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white text-sm font-medium"
            >
              {installing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
              {installing ? t("searchPage.installing") : t("searchPage.fixJsonBtn")}
            </button>
          )}
          {installLog.length > 0 && (
            <div ref={logRef} className="rounded-xl bg-zinc-950 border border-zinc-800 p-3 font-mono text-xs text-zinc-300 max-h-48 overflow-y-auto space-y-0.5">
              {installLog.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
            </div>
          )}
          {installDone === true && (
            <div className="flex items-center gap-2 text-green-400 text-sm">
              <CheckCircle className="w-4 h-4" />
              {t("searchPage.installSuccess")}
              <button onClick={load} className="ml-2 underline">{t("searchPage.reload")}</button>
            </div>
          )}
          {installDone === false && (
            <div className="flex items-center gap-2 text-red-400 text-sm">
              <XCircle className="w-4 h-4" />{t("searchPage.installFailed")}
            </div>
          )}
        </div>
      )}

      {!isRunning && !status?.http_ok && (
        <div className="rounded-2xl border border-yellow-500/30 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-600 dark:text-yellow-400">
          {t("searchPage.notRunning")}{" "}
          <code className="font-mono bg-yellow-500/10 px-1 rounded">sudo bash /opt/hydrahive/installer/modules/14_searxng.sh</code>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="section-card p-5 space-y-4">
          <h2 className="text-sm font-semibold">{t("searchPage.testSearch")}</h2>

          {(status?.engines ?? []).length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">{t("searchPage.enginesLabel")}</p>
              <div className="flex flex-wrap gap-3">
                {status!.engines.map(eng => (
                  <label key={eng} className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                    <input type="checkbox"
                      checked={engines.includes(eng)}
                      onChange={e => setEngines(prev =>
                        e.target.checked ? [...prev, eng] : prev.filter(x => x !== eng)
                      )}
                      className="rounded border-border"
                    />
                    {eng}
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleSearch()}
              placeholder={t("searchPage.searchPlaceholder")}
              className="flex-1 rounded-2xl border bg-background px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <button onClick={handleSearch}
              disabled={searching || !query.trim() || !isRunning}
              className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors">
              {searching
                ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                : <Play className="h-3.5 w-3.5" />}
              {t("searchPage.searchButton")}
            </button>
          </div>

          {result && (
            <div className="space-y-2">
              {result.error ? (
                <p className="text-sm text-destructive">{result.error}</p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    {t("searchPage.resultCount", { count: result.total ?? result.results.length })}
                    {result.suggestions && result.suggestions.length > 0 && (
                      <span className="ml-2 opacity-60">
                        {t("searchPage.suggestions")} {result.suggestions.slice(0, 3).join(", ")}
                      </span>
                    )}
                  </p>
                  <div className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
                    {result.results.map((r, i) => (
                      <div key={i} className="rounded-xl border bg-muted/30 p-3 space-y-1">
                        <div className="flex items-start justify-between gap-2">
                          <a href={r.url} target="_blank" rel="noopener noreferrer"
                            className="text-sm font-medium text-primary hover:underline line-clamp-1 flex-1">
                            {r.title || r.url}
                          </a>
                          <a href={r.url} target="_blank" rel="noopener noreferrer"
                            className="flex-shrink-0 text-muted-foreground hover:text-foreground">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </div>
                        {r.snippet && (
                          <p className="text-xs text-muted-foreground line-clamp-2">{r.snippet}</p>
                        )}
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground/50 truncate">{r.url}</span>
                          {r.engine && (
                            <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded-md bg-primary/10 text-primary/70">{r.engine}</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="section-card p-4 space-y-3">
            <h3 className="text-sm font-semibold">{t("searchPage.configuredEngines")}</h3>
            {(status?.engines ?? []).length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {status!.engines.map(eng => (
                  <span key={eng} className="px-2 py-1 text-xs rounded-lg bg-primary/10 text-primary border border-primary/20">
                    {eng}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t("searchPage.noEngines")}</p>
            )}
          </div>

          <div className="section-card p-4 space-y-2">
            <h3 className="text-sm font-semibold">{t("searchPage.agentIntegration")}</h3>
            <p className="text-xs text-muted-foreground">
              {t("searchPage.agentIntegrationDesc")}
            </p>
            <pre className="text-xs bg-muted/40 rounded-lg p-2 overflow-x-auto font-mono">
{`tools:
  - web_search`}
            </pre>
            <a href="/agents" className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-1">
              <Search className="h-3 w-3" /> {t("searchPage.toAgents")}
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusRow({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1.5">
        {ok
          ? <CheckCircle className="h-3.5 w-3.5 text-green-500" />
          : <XCircle    className="h-3.5 w-3.5 text-destructive" />}
        {detail && <span className="text-xs font-mono text-muted-foreground/60">{detail}</span>}
      </div>
    </div>
  );
}
