/**
 * OAuthUsageBar — Kompakte OAuth-Usage-Anzeige für Chat-Header
 *
 * Pollt /api/admin/system/oauth-usage alle 3s und zeigt 5h + 7d Balken.
 * Designed für den Header-Bereich über Chat-Fenstern.
 */
import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { api } from "../lib/api";

export default function OAuthUsageBar() {
  const [data, setData] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api.oauthUsage().then(d => { if (alive) setData(d as Record<string, any>); }).catch(() => {});
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!data) return (
    <div className="flex items-center gap-2 px-3 py-1 bg-muted/30 border-b text-xs text-muted-foreground/50">
      <Activity className="h-3 w-3 animate-pulse" />
      <span className="hidden sm:inline">OAuth</span>
      <span>...</span>
    </div>
  );

  if (!data.available) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-1 bg-muted/30 border-b text-xs">
      <Activity className="h-3 w-3 text-muted-foreground flex-shrink-0" />
      <span className="text-muted-foreground font-medium hidden sm:inline">OAuth</span>
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
  );
}
