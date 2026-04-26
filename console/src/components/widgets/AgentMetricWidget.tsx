import { Bot } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

type Props = WidgetProps & { agents: number | null };

export function AgentMetricWidget({ agents, className }: Props) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-2 flex items-center gap-2",
        className
      )}
    >
      <div
        className="rounded-lg p-1.5 shrink-0"
        style={{ background: "hsl(268 78% 62% / 0.15)" }}
      >
        <Bot className="h-4 w-4" style={{ color: "hsl(var(--candy-violet))" }} />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground truncate">
          Agents
        </p>
        <p
          className="text-xl font-bold"
          style={{ color: "hsl(var(--candy-violet))" }}
        >
          {agents ?? "…"}
        </p>
      </div>
    </div>
  );
}
