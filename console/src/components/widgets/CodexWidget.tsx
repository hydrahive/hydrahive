import { Cpu } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

interface CodexRateLimits {
  "x-codex-plan-type"?: string;
  "x-codex-primary-used-percent"?: string;
  "x-codex-secondary-used-percent"?: string;
}

interface CodexConfig {
  configured: boolean;
  rate_limits?: CodexRateLimits;
}

interface CodexWidgetProps extends WidgetProps {
  codex: CodexConfig | null;
}

export function CodexWidget({ codex, className }: CodexWidgetProps) {
  if (!codex?.configured) {
    return (
      <div className={cn("rounded-xl border bg-card p-3", className)}>
        <div className="flex items-center gap-2 mb-2">
          <Cpu className="h-3.5 w-3.5 text-primary" />
          <h2 className="text-xs font-semibold tracking-tight">Codex</h2>
        </div>
        <p className="text-xs text-muted-foreground">Nicht konfiguriert</p>
      </div>
    );
  }

  const rl = codex.rate_limits || {};
  const primary = parseInt(rl["x-codex-primary-used-percent"] ?? "", 10);
  const secondary = parseInt(rl["x-codex-secondary-used-percent"] ?? "", 10);
  const bars = [
    { label: "Session (5h)", pct: isNaN(primary) ? 0 : primary },
    { label: "Woche (7d)", pct: isNaN(secondary) ? 0 : secondary },
  ];

  const color = (pct: number) =>
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-orange-500" : pct >= 40 ? "bg-yellow-500" : "bg-green-500";

  return (
    <div className={cn("rounded-xl border bg-card p-3", className)}>
      <div className="flex items-center gap-2 mb-2">
        <Cpu className="h-3.5 w-3.5 text-primary" />
        <h2 className="text-xs font-semibold tracking-tight">Codex</h2>
        <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">
          {rl["x-codex-plan-type"] || "plus"}
        </span>
      </div>
      <div className="space-y-2">
        {bars.map((b) => (
          <div key={b.label} className="flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground w-20 shrink-0">
              {b.label}
            </span>
            <div className="h-1.5 flex-1 bg-muted rounded-full overflow-hidden">
              <div
                className={cn("h-full rounded-full", color(b.pct))}
                style={{ width: `${Math.min(100, b.pct)}%` }}
              />
            </div>
            <span className="text-[10px] text-muted-foreground w-8 text-right">
              {b.pct}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
