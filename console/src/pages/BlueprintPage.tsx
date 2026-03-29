import { useState } from "react";
import { Workflow, Network, ShieldCheck, Bell, Cpu } from "lucide-react";
import { cn } from "@/lib/utils";
import { ButlerPage } from "@/pages/ButlerPage";
import { ProjectArchitectTab } from "@/pages/blueprint/ProjectArchitectTab";
import { PermissionsTab }      from "@/pages/blueprint/PermissionsTab";
import { NotificationRouterTab } from "@/pages/blueprint/NotificationRouterTab";
import { WorkflowTab }          from "@/pages/blueprint/WorkflowTab";

const TABS = [
  { id: "automation",    label: "Automation",        icon: Workflow,     hint: "Butler Event-Flows" },
  { id: "architect",     label: "Projekt-Architekt", icon: Network,      hint: "Boss + Worker verdrahten" },
  { id: "permissions",   label: "Berechtigungen",    icon: ShieldCheck,  hint: "User-Rechte visuell" },
  { id: "notifications", label: "Notifications",     icon: Bell,         hint: "Alert-Routing" },
  { id: "workflow",      label: "Workflow-Kontext",  icon: Cpu,          hint: "Agent-Tools konfigurieren" },
] as const;

type TabId = typeof TABS[number]["id"];

export function BlueprintPage() {
  const [tab, setTab] = useState<TabId>("automation");

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
        {tab === "architect"     && <ProjectArchitectTab />}
        {tab === "permissions"   && <PermissionsTab />}
        {tab === "notifications" && <NotificationRouterTab />}
        {tab === "workflow"      && <WorkflowTab />}
      </div>
    </div>
  );
}
