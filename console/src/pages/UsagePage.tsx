import { useEffect, useState } from "react";
import { api, type UsageStats, type UsageProject } from "@/lib/api";
import { RefreshCw, Database, TrendingUp, Zap, DollarSign } from "lucide-react";
import { cn, agentCategory, AGENT_COLORS } from "@/lib/utils";

function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtCost(n: number): string {
  if (n === 0) return "$0";
  if (n >= 1)  return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(5)}`;
}

function StatCard({ label, value, sub, icon: Icon }: {
  label: string; value: string; sub?: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
          {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
        </div>
        <div className="rounded-xl bg-primary/10 p-2.5 text-primary">
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
}

function ModelRow({ model, data }: {
  model: string;
  data: { tokens: { input: number; output: number; cache_read: number; cache_write: number }; cost: { total: number; input: number; output: number; cache_read: number; cache_write: number } };
}) {
  const total = data.tokens.input + data.tokens.output + data.tokens.cache_read + data.tokens.cache_write;
  return (
    <tr className="border-b border-border/40 last:border-0">
      <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{model}</td>
      <td className="py-2 pr-4 tabular-nums text-right text-sm">{fmtK(data.tokens.input)}</td>
      <td className="py-2 pr-4 tabular-nums text-right text-sm">{fmtK(data.tokens.output)}</td>
      <td className="py-2 pr-4 tabular-nums text-right text-sm text-blue-500">{fmtK(data.tokens.cache_read)}</td>
      <td className="py-2 pr-4 tabular-nums text-right text-sm">{fmtK(total)}</td>
      <td className="py-2 tabular-nums text-right text-sm font-medium text-green-600 dark:text-green-400">{fmtCost(data.cost.total)}</td>
    </tr>
  );
}

function ProjectCard({ proj }: { proj: UsageProject }) {
  const [open, setOpen] = useState(false);
  const hasModels = Object.keys(proj.model_breakdown).length > 0;

  return (
    <div className={cn("rounded-2xl border shadow-sm overflow-hidden", AGENT_COLORS[agentCategory(proj.project_id)].bg, AGENT_COLORS[agentCategory(proj.project_id)].border)}>
      <button
        type="button"
        className="w-full px-5 py-4 flex items-center justify-between gap-4 hover:bg-accent/30 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <Database className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <span className="font-medium truncate">{proj.project_id}</span>
          {(() => { const c = AGENT_COLORS[agentCategory(proj.project_id)]; return <span className={`rounded-full px-1.5 py-0.5 text-xs font-medium shrink-0 ${c.badge}`}>{c.label}</span>; })()}
          <span className="text-xs text-muted-foreground shrink-0">{proj.sessions_with_usage} Sessions</span>
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-right">
            <p className="text-sm tabular-nums">{fmtK(proj.total_input + proj.total_output)} tok</p>
            <p className="text-xs text-muted-foreground">in+out</p>
          </div>
          {(proj.total_cache_read > 0) && (
            <div className="text-right">
              <p className="text-sm tabular-nums text-blue-500">{fmtK(proj.total_cache_read)} tok</p>
              <p className="text-xs text-muted-foreground">cached</p>
            </div>
          )}
          <div className="text-right min-w-[5rem]">
            <p className={cn("text-sm font-semibold tabular-nums", proj.total_cost > 0 ? "text-green-600 dark:text-green-400" : "text-muted-foreground")}>
              {fmtCost(proj.total_cost)}
            </p>
            <p className="text-xs text-muted-foreground">API-Kosten</p>
          </div>
          <span className="text-muted-foreground text-sm">{open ? "▲" : "▼"}</span>
        </div>
      </button>

      {open && hasModels && (
        <div className="px-5 pb-4 border-t border-border/40">
          <p className="text-xs uppercase tracking-widest text-muted-foreground mt-3 mb-2">Aufschlüsselung nach Modell</p>
          <table className="w-full">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th className="text-left pb-1 pr-4">Modell</th>
                <th className="text-right pb-1 pr-4">Input</th>
                <th className="text-right pb-1 pr-4">Output</th>
                <th className="text-right pb-1 pr-4 text-blue-500">Cache Hit</th>
                <th className="text-right pb-1 pr-4">Gesamt</th>
                <th className="text-right pb-1">Kosten</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(proj.model_breakdown).map(([model, data]) => (
                <ModelRow key={model} model={model} data={data} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && !hasModels && (
        <div className="px-5 pb-4 pt-3 border-t border-border/40 text-sm text-muted-foreground">
          Keine Modell-Aufschlüsselung verfügbar (alte Session-Daten ohne Token-Counts).
        </div>
      )}
    </div>
  );
}

export function UsagePage() {
  const [data, setData] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.usageStats());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const gt = data?.grand_total;
  const totalTok = (gt?.input ?? 0) + (gt?.output ?? 0);
  const cacheRatio = totalTok > 0
    ? Math.round(((gt?.cache_read ?? 0) / (totalTok + (gt?.cache_read ?? 0))) * 100)
    : 0;

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Token-Statistik</p>
          <h2 className="text-xl font-semibold">API Usage</h2>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-sm hover:bg-accent/30 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          Aktualisieren
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 dark:bg-red-950/20 px-4 py-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard
              label="Input Tokens"
              value={fmtK(gt?.input ?? 0)}
              sub="ohne Cache"
              icon={TrendingUp}
            />
            <StatCard
              label="Output Tokens"
              value={fmtK(gt?.output ?? 0)}
              sub="generiert"
              icon={Zap}
            />
            <StatCard
              label="Cache Hits"
              value={fmtK(gt?.cache_read ?? 0)}
              sub={cacheRatio > 0 ? `${cacheRatio}% gecacht` : "noch keine"}
              icon={Database}
            />
            <StatCard
              label="API-Kosten"
              value={fmtCost(gt?.cost ?? 0)}
              sub="geschätzt"
              icon={DollarSign}
            />
          </div>

          {cacheRatio > 0 && (
            <div className="rounded-xl border border-blue-200 bg-blue-50 dark:bg-blue-950/20 px-4 py-3 text-sm text-blue-700 dark:text-blue-300">
              <strong>Prompt Caching aktiv:</strong> {cacheRatio}% der Tokens aus Cache — ohne Caching wären die Kosten ca.{" "}
              <strong>{fmtCost((gt?.cost ?? 0) / Math.max(0.1, 1 - cacheRatio / 100))}</strong> gewesen.
            </div>
          )}

          <div className="space-y-3">
            <p className="text-sm font-medium text-muted-foreground uppercase tracking-widest">
              Projekte ({data.projects.length})
            </p>
            {data.projects.length === 0 ? (
              <div className="rounded-2xl border border-border/60 bg-card px-5 py-8 text-center text-muted-foreground text-sm">
                Noch keine Token-Daten vorhanden. Token-Counts werden ab dem nächsten Agent-Gespräch gespeichert.
              </div>
            ) : (
              data.projects
                .sort((a, b) => b.total_cost - a.total_cost)
                .map(proj => <ProjectCard key={proj.project_id} proj={proj} />)
            )}
          </div>

          <div className="rounded-xl border border-border/60 bg-card/50 px-5 py-4">
            <p className="text-xs text-muted-foreground uppercase tracking-widest mb-2">Preisreferenz ($/1M Tokens)</p>
            <div className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-3">
              {Object.entries(data.pricing_ref).map(([model, p]) => (
                <div key={model} className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="font-mono text-muted-foreground truncate">{model}</span>
                  <span className="tabular-nums shrink-0">in ${p.input} / out ${p.output}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <RefreshCw className="h-6 w-6 animate-spin mr-2" />
          Lade Usage-Daten…
        </div>
      )}
    </div>
  );
}
