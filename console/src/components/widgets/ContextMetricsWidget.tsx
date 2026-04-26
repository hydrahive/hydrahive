import { Brain } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

interface MetricData {
  cache_hit_rate: number;
  llm_call_count: number;
  total_input_tokens: number;
}

interface ProjectMeta {
  name?: string;
}

interface ContextMetricsWidgetProps extends WidgetProps {
  sessionMetrics: Record<string, MetricData>;
  projectMap: Record<string, ProjectMeta>;
}

export function ContextMetricsWidget({
  sessionMetrics,
  projectMap,
  className,
}: ContextMetricsWidgetProps) {
  const entries = Object.entries(sessionMetrics).slice(0, 2);

  return (
    <div className={cn("flex flex-col h-full", className)}>
      <div className="flex items-center gap-2 mb-3">
        <Brain className="h-3.5 w-3.5 text-primary" />
        <h2 className="text-xs font-semibold tracking-tight">Context-Metriken</h2>
      </div>
      <div className="space-y-2 flex-1">
        {entries.map(([pid, m]) => (
          <div key={pid} className="rounded-lg border bg-muted/30 p-3">
            <p className="text-[10px] font-medium text-muted-foreground mb-1.5">
              {projectMap[pid]?.name || pid}
            </p>
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">Cache-Hit</span>
                <span
                  className={
                    m.cache_hit_rate > 0.5
                      ? "text-green-400"
                      : m.cache_hit_rate > 0.2
                      ? "text-yellow-400"
                      : "text-red-400"
                  }
                >
                  {(m.cache_hit_rate * 100).toFixed(0)}%
                </span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${(m.cache_hit_rate * 100).toFixed(0)}%`,
                    background:
                      m.cache_hit_rate > 0.5
                        ? "hsl(150 70% 52%)"
                        : m.cache_hit_rate > 0.2
                        ? "hsl(38 92% 50%)"
                        : "hsl(5 68% 56%)",
                  }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>LLM-Calls: {m.llm_call_count}</span>
                <span>{(m.total_input_tokens / 1000).toFixed(1)}k in</span>
              </div>
            </div>
          </div>
        ))}
        {entries.length === 0 && (
          <p className="text-xs text-muted-foreground py-2 text-center">
            Keine Metriken
          </p>
        )}
      </div>
    </div>
  );
}
