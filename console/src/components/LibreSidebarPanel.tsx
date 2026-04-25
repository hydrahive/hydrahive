/**
 * LibreSidebarPanel — FreeStyle Libre 3 Glukose-Widget (#912)
 * Zeigt aktuellen Wert, Trend und 24h-Verlauf.
 * Erscheint nur wenn /api/libre/status configured=true.
 */
import { useEffect, useState, useCallback } from "react";
import { Activity, RefreshCw, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

interface GlucoseReading {
  value: number;
  unit: string;
  trend: string;
  trend_num: number;
  timestamp: string;
  color: "green" | "yellow" | "red";
}

interface HistoryEntry {
  value: number;
  unit: string;
  trend: string;
  color: "green" | "yellow" | "red";
  timestamp: string;
}

const COLOR_CLASS: Record<string, string> = {
  green:  "text-emerald-400",
  yellow: "text-yellow-400",
  red:    "text-red-400",
};

const COLOR_BG: Record<string, string> = {
  green:  "bg-emerald-500/10 border-emerald-500/20",
  yellow: "bg-yellow-500/10 border-yellow-500/20",
  red:    "bg-red-500/10 border-red-500/20",
};

function formatTs(ts: string): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts.replace(" ", "T"));
    return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  } catch { return ts.slice(11, 16) || "—"; }
}

export function LibreSidebarPanel() {
  const [current, setCurrent] = useState<GlucoseReading | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [cur, hist] = await Promise.all([
        api.get<GlucoseReading>("/libre/current"),
        api.get<{ readings: HistoryEntry[] }>("/libre/history?hours=8").then(r => r.readings ?? []),
      ]);
      setCurrent(cur);
      setHistory(hist.slice(-24).reverse()); // neueste zuerst
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e?.message ?? "Fehler beim Laden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const iv = setInterval(() => void load(), 5 * 60 * 1000); // alle 5 min
    return () => clearInterval(iv);
  }, [load]);

  if (loading) return (
    <div className="flex items-center justify-center py-8 text-white/30">
      <RefreshCw className="h-4 w-4 animate-spin mr-2" />
      <span className="text-xs">Lade Glukose…</span>
    </div>
  );

  if (error) return (
    <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-400 flex items-start gap-2">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
      <span>{error}</span>
    </div>
  );

  if (!current) return null;

  return (
    <div className="space-y-3">
      {/* Aktueller Wert */}
      <div className={`rounded-2xl border p-4 ${COLOR_BG[current.color]}`}>
        <div className="flex items-center justify-between mb-2">
          <p className="text-[0.65rem] uppercase tracking-[0.16em] text-white/40 flex items-center gap-1">
            <Activity className="h-3 w-3" /> Aktuell
          </p>
          <button onClick={() => void load()} className="text-white/30 hover:text-white/70 transition-colors">
            <RefreshCw className="h-3 w-3" />
          </button>
        </div>
        <div className="flex items-end gap-2">
          <span className={`text-4xl font-bold tabular-nums ${COLOR_CLASS[current.color]}`}>
            {current.value}
          </span>
          <span className="text-lg text-white/50 mb-0.5">{current.unit}</span>
          <span className={`text-2xl mb-0.5 ${COLOR_CLASS[current.color]}`}>{current.trend}</span>
        </div>
        <p className="text-[0.65rem] text-white/30 mt-1">{formatTs(current.timestamp)}</p>
      </div>

      {/* Verlauf */}
      {history.length > 0 && (
        <div className="card-candy-border rounded-2xl border bg-background/75 p-3">
          <p className="text-[0.65rem] uppercase tracking-[0.16em] text-white/40 mb-2">Verlauf (8h)</p>
          <div className="space-y-1 max-h-52 overflow-y-auto">
            {history.map((r, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-0.5">
                <span className="text-white/30 font-mono w-10">{formatTs(r.timestamp)}</span>
                <span className={`font-medium tabular-nums ${COLOR_CLASS[r.color]}`}>
                  {r.value} <span className="text-white/20">{r.unit}</span>
                </span>
                <span className={`w-5 text-center ${COLOR_CLASS[r.color]}`}>{r.trend}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {lastRefresh && (
        <p className="text-[0.6rem] text-white/20 text-center">
          Aktualisiert {lastRefresh.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} · alle 5 min
        </p>
      )}
    </div>
  );
}
