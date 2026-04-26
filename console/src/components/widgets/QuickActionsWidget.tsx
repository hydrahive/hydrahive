import { Plus, FolderKanban, MessageSquare, ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";

export function QuickActionsWidget({ className }: WidgetProps) {
  const navigate = useNavigate();

  return (
    <div className={cn("flex flex-col h-full", className)}>
      <h2 className="text-xs font-semibold tracking-tight mb-3">
        Quick Actions
      </h2>
      <div className="space-y-2 flex-1">
        <button
          type="button"
          onClick={() => navigate("/agents/new")}
          className="w-full flex items-center justify-between gap-3 rounded-xl border bg-card px-3 py-2.5 text-sm transition-colors hover:bg-accent/10 hover:border-primary/30"
        >
          <span className="flex items-center gap-2">
            <Plus className="h-4 w-4 text-primary" />
            Neuer Agent
          </span>
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
        </button>
        <button
          type="button"
          onClick={() => navigate("/projects?new=1")}
          className="w-full flex items-center justify-between gap-3 rounded-xl border bg-card px-3 py-2.5 text-sm transition-colors hover:bg-accent/10 hover:border-primary/30"
        >
          <span className="flex items-center gap-2">
            <FolderKanban className="h-4 w-4 text-primary" />
            Neues Projekt
          </span>
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
        </button>
        <button
          type="button"
          onClick={() => navigate("/my-agent")}
          className="w-full flex items-center justify-between gap-3 rounded-xl border bg-card px-3 py-2.5 text-sm transition-colors hover:bg-accent/10 hover:border-primary/30"
        >
          <span className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-primary" />
            Chat öffnen
          </span>
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>
    </div>
  );
}
