import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

export interface AttentionItem {
  tone: "critical" | "warn" | "ok";
  title: string;
  detail: string;
}

interface AttentionWidgetProps extends WidgetProps {
  items: AttentionItem[];
}

export function AttentionWidget({ items, className }: AttentionWidgetProps) {
  const hasCritical = items.some((i) => i.tone === "critical");
  const hasWarn = items.some((i) => i.tone === "warn");
  const borderClass = hasCritical
    ? "border-l-4 border-l-destructive"
    : hasWarn
    ? "border-l-4 border-l-amber-400"
    : "border-l-4 border-l-green-400";
  const iconClass = hasCritical
    ? "text-destructive"
    : hasWarn
    ? "text-amber-400"
    : "text-green-400";

  return (
    <div className={cn("rounded-xl border bg-card p-3", borderClass, className)}>
      <div className="flex items-center gap-2 mb-3">
        <AlertTriangle className={cn("h-3.5 w-3.5", iconClass)} />
        <h2 className="text-xs font-semibold tracking-tight">Attention</h2>
      </div>
      <div className="space-y-1.5">
        {items.map((item, idx) => (
          <div
            key={idx}
            className={cn(
              "rounded-lg border px-2.5 py-2",
              item.tone === "critical"
                ? "border-destructive/30 bg-destructive/5"
                : item.tone === "warn"
                ? "border-amber-400/30 bg-amber-500/5"
                : "border-green-400/20 bg-green-500/5"
            )}
          >
            <p className="text-xs font-medium">{item.title}</p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">{item.detail}</p>
          </div>
        ))}
        {items.length === 0 && (
          <p className="text-xs text-muted-foreground">Alles klar ✓</p>
        )}
      </div>
    </div>
  );
}
