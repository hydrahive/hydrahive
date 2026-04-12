import { useState, lazy, Suspense } from "react";
import { Workflow, Bell, Cpu, Bot, FolderOpen, PenTool } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";
import { useTranslation } from "react-i18next";
import { ButlerPage } from "@/pages/ButlerPage";
import { NotificationRouterTab } from "@/pages/blueprint/NotificationRouterTab";
import { WorkflowTab }          from "@/pages/blueprint/WorkflowTab";
import { AgentBlueprintTab }    from "@/pages/blueprint/AgentBlueprintTab";
import { FilePipelineTab }      from "@/pages/blueprint/FilePipelineTab";
const ScratchpadTab = lazy(() => import("@/pages/blueprint/ScratchpadTab").then(m => ({ default: m.ScratchpadTab })));

const ALL_TABS = [
  { id: "automation",      i18nKey: "automation",      icon: Workflow,    minGroup: "chatter" },
  { id: "pipelines",       i18nKey: "pipelines",       icon: FolderOpen,  minGroup: "admin" },
  { id: "workflow",        i18nKey: "workflow",         icon: Cpu,         minGroup: "standard" },
  { id: "agentblueprint",  i18nKey: "agentblueprint",  icon: Bot,         minGroup: "dev" },
  { id: "scratchpad",      i18nKey: "scratchpad",       icon: PenTool,     minGroup: "chatter" },
  { id: "notifications",   i18nKey: "notifications",   icon: Bell,        minGroup: "admin" },
] as const;

const GROUP_RANK: Record<string, number> = {
  chatter: 1, standard: 2, learning: 3, dev: 4, admin: 99,
};

type TabId = typeof ALL_TABS[number]["id"];

export function BlueprintPage() {
  const { t } = useTranslation();
  const { user, isAdmin } = useAuth();
  const group = isAdmin ? "admin" : (user?.group ?? "standard");
  const rank  = GROUP_RANK[group] ?? 2;

  const TABS = ALL_TABS.filter(t =>
    t.minGroup === "admin" ? isAdmin : (GROUP_RANK[t.minGroup] ?? 0) <= rank
  );

  const [tab, setTab] = useState<TabId>(() => TABS[0]?.id ?? "automation");

  return (
    <div className="flex flex-col h-full -mx-4 -my-4 md:-mx-6 md:-my-6 lg:-mx-8 lg:-my-8">
      {/* Info block */}
      <div className="px-4 pt-4 md:px-6 lg:px-8">
        <div className="mb-4 rounded-xl border bg-muted/30 p-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("blueprint.infoTitle")}</h3>
          <p className="text-xs text-muted-foreground leading-relaxed">{t("blueprint.infoText")}</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-0.5 px-4 pt-3 pb-0 border-b border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] shrink-0 overflow-x-auto">
        {TABS.map(tb => (
          <button
            key={tb.id}
            onClick={() => setTab(tb.id)}
            title={t(`blueprint.${tb.i18nKey}Hint`)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg border-b-2 -mb-px whitespace-nowrap transition-colors",
              tab === tb.id
                ? "border-indigo-500 text-white bg-zinc-900"
                : "border-transparent text-white/40 hover:text-white/70 hover:bg-white/5"
            )}
          >
            <tb.icon className="h-3.5 w-3.5 shrink-0" />
            {t(`blueprint.${tb.i18nKey}`)}
          </button>
        ))}
      </div>

      {/* Tab content — full height */}
      <div className="flex-1 overflow-hidden bg-zinc-950">
        {tab === "automation"    && <ButlerPage />}
        {tab === "pipelines"     && <FilePipelineTab />}
        {tab === "notifications"  && <NotificationRouterTab />}
        {tab === "workflow"       && <WorkflowTab />}
        {tab === "agentblueprint" && <AgentBlueprintTab />}
        {tab === "scratchpad"     && <Suspense fallback={<div className="flex items-center justify-center h-full text-white/20">{t("blueprint.loading")}</div>}><ScratchpadTab /></Suspense>}
      </div>
    </div>
  );
}
