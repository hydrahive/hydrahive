import { useState, lazy, Suspense } from "react";
import { Workflow, Network, ShieldCheck, Bell, Cpu, Bot, FolderOpen, PenTool } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";
import { ButlerPage } from "@/pages/ButlerPage";
import { ProjectArchitectTab } from "@/pages/blueprint/ProjectArchitectTab";
import { PermissionsTab }      from "@/pages/blueprint/PermissionsTab";
import { NotificationRouterTab } from "@/pages/blueprint/NotificationRouterTab";
import { WorkflowTab }          from "@/pages/blueprint/WorkflowTab";
import { AgentBlueprintTab }    from "@/pages/blueprint/AgentBlueprintTab";
import { FilePipelineTab }      from "@/pages/blueprint/FilePipelineTab";
const ScratchpadTab = lazy(() => import("@/pages/blueprint/ScratchpadTab").then(m => ({ default: m.ScratchpadTab })));

const ALL_TABS = [
  { id: "automation",      label: "Automation",        icon: Workflow,    hint: "Butler Event-Flows",                   minGroup: "chatter" },
  { id: "pipelines",       label: "Datei-Pipelines",   icon: FolderOpen,  hint: "Dateien sortieren und verarbeiten",    minGroup: "admin" },
  { id: "architect",       label: "Projekt-Architekt", icon: Network,     hint: "Boss + Worker verdrahten",             minGroup: "standard" },
  { id: "workflow",        label: "Projekt-Workflow",  icon: Cpu,         hint: "Arbeitsablauf für Agenten definieren", minGroup: "standard" },
  { id: "agentblueprint",  label: "Agent-Blueprint",   icon: Bot,         hint: "Repos, Skills, Memory verdrahten",     minGroup: "dev" },
  { id: "scratchpad",      label: "Scratchpad",        icon: PenTool,     hint: "Freies Whiteboard — Ideen skizzieren", minGroup: "chatter" },
  { id: "notifications",   label: "Notifications",     icon: Bell,        hint: "Alert-Routing",                        minGroup: "admin" },
  { id: "permissions",     label: "Berechtigungen",    icon: ShieldCheck, hint: "User-Rechte visuell",                  minGroup: "admin" },
] as const;

const GROUP_RANK: Record<string, number> = {
  chatter: 1, standard: 2, learning: 3, dev: 4, admin: 99,
};

type TabId = typeof ALL_TABS[number]["id"];

export function BlueprintPage() {
  const { user, isAdmin } = useAuth();
  const group = isAdmin ? "admin" : (user?.group ?? "standard");
  const rank  = GROUP_RANK[group] ?? 2;

  const TABS = ALL_TABS.filter(t =>
    t.minGroup === "admin" ? isAdmin : (GROUP_RANK[t.minGroup] ?? 0) <= rank
  );

  const [tab, setTab] = useState<TabId>(() => TABS[0]?.id ?? "automation");

  return (
    <div className="flex flex-col h-full -mx-4 -my-4 md:-mx-6 md:-my-6 lg:-mx-8 lg:-my-8">
      {/* Tab bar */}
      <div className="flex items-center gap-0.5 px-4 pt-3 pb-0 border-b border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] shrink-0 overflow-x-auto">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            title={t.hint}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg border-b-2 -mb-px whitespace-nowrap transition-colors",
              tab === t.id
                ? "border-indigo-500 text-white bg-zinc-900"
                : "border-transparent text-white/40 hover:text-white/70 hover:bg-white/5"
            )}
          >
            <t.icon className="h-3.5 w-3.5 shrink-0" />
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content — full height */}
      <div className="flex-1 overflow-hidden bg-zinc-950">
        {tab === "automation"    && <ButlerPage />}
        {tab === "pipelines"     && <FilePipelineTab />}
        {tab === "architect"     && <ProjectArchitectTab />}
        {tab === "permissions"   && <PermissionsTab />}
        {tab === "notifications"  && <NotificationRouterTab />}
        {tab === "workflow"       && <WorkflowTab />}
        {tab === "agentblueprint" && <AgentBlueprintTab />}
        {tab === "scratchpad"     && <Suspense fallback={<div className="flex items-center justify-center h-full text-white/20">Laden...</div>}><ScratchpadTab /></Suspense>}
      </div>
    </div>
  );
}
