import { Radar } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

interface ActivityEntry {
  id: string;
  action: string;
  user: string;
  timestamp: string;
}

interface ProjectSignal {
  id: string;
  tone?: "warn" | "info";
  title: string;
  summary: string;
  meta: string;
}

interface ActivityStreamWidgetProps extends WidgetProps {
  activity: ActivityEntry[];
  projectSignals: ProjectSignal[];
  maxItems?: number;
}

export function ActivityStreamWidget({
  activity,
  projectSignals,
  maxItems = 8,
  className,
}: ActivityStreamWidgetProps) {
  return (
    <div className={cn("flex flex-col h-full", className)}>
      <div className="flex items-center gap-2 mb-3">
        <Radar className="h-3.5 w-3.5 text-primary" />
        <h2 className="text-xs font-semibold tracking-tight">Activity</h2>
      </div>
      <div className="flex-1 space-y-1 max-h-40 overflow-y-auto">
        {activity.slice(0, maxItems).map((entry) => (
          <div
            key={entry.id}
            className="flex items-start gap-2 py-1 border-b border-border/30 last:border-0"
          >
            <span className="dot mt-1.5 bg-primary shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium truncate">{entry.action}</p>
              <p className="text-[10px] text-muted-foreground">
                {entry.user} ·{" "}
                {new Date(entry.timestamp).toLocaleTimeString("de-DE")}
              </p>
            </div>
          </div>
        ))}
        {projectSignals.slice(0, maxItems).map((proj) => (
          <div
            key={proj.id}
            className="flex items-start gap-2 py-1 border-b border-border/30 last:border-0"
          >
            <span
              className={cn(
                "dot mt-1.5 shrink-0",
                proj.tone === "warn" ? "bg-amber-400" : "bg-green-400"
              )}
            />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium truncate">{proj.title}</p>
              <p className="text-[10px] text-muted-foreground">
                {proj.summary} · {proj.meta}
              </p>
            </div>
          </div>
        ))}
        {activity.length === 0 && projectSignals.length === 0 && (
          <p className="text-xs text-muted-foreground py-4 text-center">
            Noch keine Activity
          </p>
        )}
      </div>
    </div>
  );
}
