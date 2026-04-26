import { FolderKanban } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

type Props = WidgetProps & { projects: number | null };

export function ProjectMetricWidget({ projects, className }: Props) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-2 flex items-center gap-2",
        className
      )}
    >
      <div
        className="rounded-lg p-1.5 shrink-0"
        style={{ background: "hsl(188 90% 52% / 0.15)" }}
      >
        <FolderKanban
          className="h-4 w-4"
          style={{ color: "hsl(var(--candy-cyan))" }}
        />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground truncate">
          Projects
        </p>
        <p
          className="text-xl font-bold"
          style={{ color: "hsl(var(--candy-cyan))" }}
        >
          {projects ?? "…"}
        </p>
      </div>
    </div>
  );
}
