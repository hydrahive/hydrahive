/**
 * OAuthUsageBar — Kompakte OAuth-Usage-Anzeige für Chat-Header
 *
 * Pollt /api/admin/system/oauth-usage alle 3s und zeigt 5h + 7d Balken.
 * Zeigt optional auch OpenAI Codex Token-Status wenn konfiguriert.
 * Designed für den Header-Bereich über Chat-Fenstern.
 */
import { useEffect, useState } from "react";
import { Activity, Cpu } from "lucide-react";
import { api } from "../lib/api";

export default function OAuthUsageBar() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [codex, setCodex] = useState<{ configured: boolean; account_id: string | null; models?: string[] } | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api.oauthUsage().then(d => { if (alive) setData(d as Record<string, any>); }).catch(() => {});
    };
    poll();
    const t = setInterval(poll, 3000);
    // Codex-Status einmalig laden
    api.openaiCodexStatus().then(d => { if (alive) setCodex(d); }).catch(() => {});
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!data) return (
    <div className="flex items-center gap-2 px-3 py-1 bg-muted/30 border-b text-xs text-muted-foreground/50">
      <Activity className="h-3 w-3 animate-pulse" />
      <span className="hidden sm:inline">OAuth</span>
      <span>...</span>
    </div>
  );

  const showClaude = data.available;
  const showCodex = codex?.configured;
  if (!showClaude && !showCodex) return null;

  return (
    <div className="flex flex-col border-b bg-muted/30">
      {/* Claude OAuth */}
      {showClaude && (
        <div className="flex items-center gap-2 px-3 py-1 text-xs">
          <Activity className="h-3 w-3 text-muted-foreground flex-shrink-0" />
          <span className="text-muted-foreground font-medium hidden sm:inline">Claude</span>
          {["5h", "7d"].map(w => {
            const d = data[w] as { utilization_pct: number; label: string; reset?: string } | undefined;
            if (!d) return null;
            const pct = d.utilization_pct ?? 0;
            const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";
            return (
              <div key={w} className="flex items-center gap-1.5">
                <span className="text-muted-foreground/70 whitespace-nowrap">{w}:</span>
                <div className="h-1.5 w-16 sm:w-20 bg-muted rounded-full overflow-hidden">
                  <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${Math.min(100, pct)}%` }} />
                </div>
                <span className={`w-7 text-right tabular-nums ${pct >= 90 ? "text-red-500 font-medium" : "text-muted-foreground/70"}`}>{pct}%</span>
              </div>
            );
          })}
          {data.status && (
            <span className={`px-1.5 py-0.5 rounded-full text-[10px] leading-none ${
              data.status === "allowed" ? "bg-green-500/15 text-green-500" :
              data.status === "allowed_warning" ? "bg-orange-500/15 text-orange-500" :
              "bg-destructive/15 text-destructive"
            }`}>{String(data.status)}</span>
          )}
        </div>
      )}
      {/* OpenAI Codex */}
      {showCodex && (
        <div className={`flex items-center gap-2 px-3 py-1 text-xs ${showClaude ? "border-t border-border/30" : ""}`}>
          <Cpu className="h-3 w-3 text-muted-foreground flex-shrink-0" />
          <span className="text-muted-foreground font-medium hidden sm:inline">Codex</span>
          {(() => {
            const rl = (codex as any)?.rate_limits;
            const primary = parseInt(rl?.["x-codex-primary-used-percent"] ?? "", 10);
            const secondary = parseInt(rl?.["x-codex-secondary-used-percent"] ?? "", 10);
            if (!isNaN(primary) || !isNaN(secondary)) {
              const bars = [
                { label: "5h", pct: isNaN(primary) ? 0 : primary },
                { label: "7d", pct: isNaN(secondary) ? 0 : secondary },
              ];
              return bars.map(b => {
                const color = b.pct >= 90 ? "bg-red-500" : b.pct >= 70 ? "bg-orange-500" : b.pct >= 40 ? "bg-yellow-500" : "bg-green-500";
                return (
                  <div key={b.label} className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/70 whitespace-nowrap">{b.label}:</span>
                    <div className="h-1.5 w-16 sm:w-20 bg-muted rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${Math.min(100, b.pct)}%` }} />
                    </div>
                    <span className={`w-7 text-right tabular-nums ${b.pct >= 90 ? "text-red-500 font-medium" : "text-muted-foreground/70"}`}>{b.pct}%</span>
                  </div>
                );
              });
            }
            return <span className="px-1.5 py-0.5 rounded-full text-[10px] leading-none bg-green-500/15 text-green-500">active</span>;
          })()}
          <span className={`px-1.5 py-0.5 rounded-full text-[10px] leading-none bg-blue-500/15 text-blue-400`}>
            {(codex as any)?.rate_limits?.["x-codex-plan-type"] || "plus"}
          </span>
        </div>
      )}
    </div>
  );
}
