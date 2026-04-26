import { Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

interface MiniMaxModel {
  name: string;
  label: string;
  interval_pct: number;
  interval_reset_in_s: number;
  weekly_pct: number;
}

interface MiniMaxInfo {
  available: boolean;
  models?: MiniMaxModel[];
}

interface MiniMaxWidgetProps extends WidgetProps {
  minimax: MiniMaxInfo | null;
}

function fmtReset(s: number) {
  if (s <= 0) return "jetzt";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function MiniMaxWidget({ minimax, className }: MiniMaxWidgetProps) {
  if (!minimax?.available) {
    return (
      <div className={cn("rounded-xl border bg-card p-3", className)}>
        <div className="flex items-center gap-2 mb-2">
          <Zap className="h-3.5 w-3.5 text-primary" />
          <h2 className="text-xs font-semibold tracking-tight">MiniMax</h2>
        </div>
        <p className="text-xs text-muted-foreground">Nicht verfügbar</p>
      </div>
    );
  }

  const iColor = (pct: number) =>
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";

  return (
    <div className={cn("rounded-xl border bg-card p-3", className)}>
      <div className="flex items-center gap-2 mb-2">
        <Zap className="h-3.5 w-3.5 text-primary" />
        <h2 className="text-xs font-semibold tracking-tight">MiniMax</h2>
      </div>
      <div className="space-y-2">
        {(minimax.models ?? []).map((m) => (
          <div key={m.name} className="border-b border-border/30 last:border-0 pb-2 last:pb-0">
            <div className="flex items-center justify-between text-[10px] mb-1.5">
              <span className="font-medium">{m.label}</span>
              <span className="text-muted-foreground">
                Reset {fmtReset(m.interval_reset_in_s)}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-muted-foreground w-4">5h</span>
                <div className="h-1 flex-1 bg-muted rounded-full overflow-hidden">
                  <div
                    className={cn("h-full rounded-full", iColor(m.interval_pct))}
                    style={{ width: `${Math.min(100, m.interval_pct)}%` }}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground w-6">
                  {m.interval_pct}%
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-muted-foreground w-4">7d</span>
                <div className="h-1 flex-1 bg-muted rounded-full overflow-hidden">
                  <div
                    className={cn("h-full rounded-full", iColor(m.weekly_pct))}
                    style={{ width: `${Math.min(100, m.weekly_pct)}%` }}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground w-6">
                  {m.weekly_pct}%
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
