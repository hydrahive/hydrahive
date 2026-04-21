/**
 * OAuthUsageBar — Kompakte OAuth-Usage-Anzeige für Chat-Header
 *
 * Pollt /api/admin/system/oauth-usage alle 3s und zeigt 5h + 7d Balken
 * für Anthropic Claude. Zeigt optional OpenAI Codex Token-Status.
 * #805: Zeigt zusätzlich MiniMax Token-Plan Usage (Text/TTS/Music/Video).
 */
import { useEffect, useState } from "react";
import { Activity, Cpu, Sparkles } from "lucide-react";
import { api } from "../lib/api";

type MinimaxModel = {
  name: string;
  label: string;
  interval_total: number;
  interval_used: number;
  interval_pct: number;
  interval_reset_in_s: number;
  weekly_total: number;
  weekly_used: number;
  weekly_pct: number;
};

type MinimaxUsage = {
  available: boolean;
  reason?: string;
  fetched_at?: string;
  models?: MinimaxModel[];
};

// Welche MiniMax-Kategorien werden in der Bar angezeigt + in welcher Reihenfolge.
// Innerhalb einer Kategorie: nur das Modell mit der höchsten interval_pct zeigen
// (z.B. music-2.5 voll + music-2.6 leer → music-2.5 anzeigen).
const MINIMAX_CATEGORIES: Array<{ key: string; label: string }> = [
  { key: "text",  label: "Text" },
  { key: "video", label: "Video" },
  { key: "music", label: "Music" },
  { key: "tts",   label: "TTS" },
];

function pickTopPerCategory(models: MinimaxModel[]): MinimaxModel[] {
  const byKey: Record<string, MinimaxModel> = {};
  for (const m of models) {
    if (!byKey[m.name] || m.interval_pct > byKey[m.name].interval_pct) {
      byKey[m.name] = m;
    }
  }
  return MINIMAX_CATEGORIES
    .map(c => byKey[c.key])
    .filter((m): m is MinimaxModel => Boolean(m));
}

function pctColor(pct: number): string {
  if (pct >= 90) return "bg-red-500";
  if (pct >= 70) return "bg-orange-500";
  if (pct >= 40) return "bg-yellow-500";
  return "bg-green-500";
}

function categoryLabel(name: string): string {
  return MINIMAX_CATEGORIES.find(c => c.key === name)?.label ?? name;
}

export default function OAuthUsageBar() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [codex, setCodex] = useState<{ configured: boolean; account_id: string | null; models?: string[] } | null>(null);
  const [minimax, setMinimax] = useState<MinimaxUsage | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api.oauthUsage().then(d => { if (alive) setData(d as Record<string, any>); }).catch(() => {});
      api.minimaxUsage().then(d => { if (alive) setMinimax(d); }).catch(() => {});
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
  const showMinimax = minimax?.available && (minimax?.models?.length ?? 0) > 0;
  const minimaxTop = showMinimax ? pickTopPerCategory(minimax!.models!) : [];
  if (!showClaude && !showCodex && !showMinimax) return null;

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
      {/* MiniMax Token-Plan (#805) */}
      {showMinimax && (
        <div className={`flex items-center gap-2 px-3 py-1 text-xs flex-wrap ${(showClaude || showCodex) ? "border-t border-border/30" : ""}`}>
          <Sparkles className="h-3 w-3 text-muted-foreground flex-shrink-0" />
          <span className="text-muted-foreground font-medium hidden sm:inline">MiniMax</span>
          {minimaxTop.map(m => {
            const color = pctColor(m.interval_pct);
            const cat = categoryLabel(m.name);
            const textColor = m.interval_pct >= 90
              ? "text-red-500 font-medium"
              : "text-muted-foreground/70";
            return (
              <div
                key={m.label}
                className="flex items-center gap-1.5"
                title={`${m.label} — ${m.interval_used}/${m.interval_total} im Fenster, ${m.weekly_used}/${m.weekly_total} diese Woche`}
              >
                <span className="text-muted-foreground/70 whitespace-nowrap">{cat}:</span>
                <div className="h-1.5 w-14 sm:w-16 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${color}`}
                    style={{ width: `${Math.min(100, m.interval_pct)}%` }}
                  />
                </div>
                <span className={`w-8 text-right tabular-nums ${textColor}`}>
                  {m.interval_pct < 10 ? m.interval_pct.toFixed(0) : Math.round(m.interval_pct)}%
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
