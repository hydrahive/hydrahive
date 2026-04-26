import { Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { WidgetProps } from "./WidgetShell";

interface OAuthUsage {
  available: boolean;
  message?: string;
  status?: string;
  "5h"?: { utilization_pct: number; label: string };
  "7d"?: { utilization_pct: number; label: string };
}

interface OAuthWidgetProps extends WidgetProps {
  oauthUsage: OAuthUsage | null;
}

export function OAuthWidget({ oauthUsage, className }: OAuthWidgetProps) {
  if (!oauthUsage || (!oauthUsage.available && !oauthUsage.message)) {
    return (
      <div className={cn("rounded-xl border bg-card p-3", className)}>
        <div className="flex items-center gap-2 mb-2">
          <Activity className="h-3.5 w-3.5 text-primary" />
          <h2 className="text-xs font-semibold tracking-tight">Claude OAuth</h2>
        </div>
        <p className="text-xs text-muted-foreground">Lädt…</p>
      </div>
    );
  }

  if (!oauthUsage.available) {
    return (
      <div className={cn("rounded-xl border bg-card p-3", className)}>
        <div className="flex items-center gap-2 mb-2">
          <Activity className="h-3.5 w-3.5 text-primary" />
          <h2 className="text-xs font-semibold tracking-tight">Claude OAuth</h2>
        </div>
        <p className="text-xs text-muted-foreground">{oauthUsage.message}</p>
      </div>
    );
  }

  const color = (pct: number) =>
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";

  const statusClass =
    oauthUsage.status === "allowed"
      ? "bg-green-500/15 text-green-400"
      : oauthUsage.status === "allowed_warning"
      ? "bg-orange-500/15 text-orange-400"
      : "bg-destructive/15 text-destructive";

  return (
    <div className={cn("rounded-xl border bg-card p-3", className)}>
      <div className="flex items-center gap-2 mb-2">
        <Activity className="h-3.5 w-3.5 text-primary" />
        <h2 className="text-xs font-semibold tracking-tight">Claude OAuth</h2>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {(["5h", "7d"] as const).map((w) => {
          const d = oauthUsage[w];
          if (!d) return null;
          return (
            <div key={w} className="flex items-center gap-2 min-w-[120px]">
              <span className="text-[10px] text-muted-foreground w-8">{d.label}</span>
              <div className="h-1.5 flex-1 bg-muted rounded-full overflow-hidden max-w-[80px]">
                <div
                  className={cn("h-full rounded-full", color(d.utilization_pct))}
                  style={{ width: `${Math.min(100, d.utilization_pct)}%` }}
                />
              </div>
              <span className="text-[10px] text-muted-foreground w-6 text-right">
                {d.utilization_pct}%
              </span>
            </div>
          );
        })}
        <span className={cn("text-[10px] px-2 py-0.5 rounded-full ml-auto", statusClass)}>
          {oauthUsage.status}
        </span>
      </div>
    </div>
  );
}
