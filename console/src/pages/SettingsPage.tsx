import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Cpu, GitBranch, Github, Network, Settings, Mail, Archive, ArrowRightLeft, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/utils";
import { ConfigHubPage } from "@/pages/ConfigHubPage";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import GiteaConfigPage from "@/pages/GiteaConfigPage";
import { GitHubConfigPage } from "@/pages/GitHubConfigPage";
import { VpnPage } from "@/pages/VpnPage";
import { KasConfigPage } from "@/pages/KasConfigPage";
import { BackupPage } from "@/pages/BackupPage";
import { MigrationPage } from "@/pages/MigrationPage";
import { useTranslation } from "react-i18next";

type TabId = "overview" | "llm" | "gitea" | "github" | "vpn" | "kas" | "backup" | "migration";

export function SettingsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  // #331: Tab immer direkt aus URL ableiten — kein eigener State
  const rawTab = searchParams.get("tab") as TabId | null;
  const active: TabId = rawTab && ["overview","llm","gitea","github","vpn","kas","backup","migration"].includes(rawTab)
    ? rawTab : "overview";
  const setActive = useCallback((id: TabId) => {
    setSearchParams({ tab: id }, { replace: true });
  }, [setSearchParams]);

  const TABS: { id: TabId; label: string; icon: React.ElementType; component: React.ComponentType }[] = useMemo(() => [
    { id: "overview",  label: t("settings.tabOverview", { defaultValue: "Übersicht" }), icon: LayoutDashboard,  component: ConfigHubPage },
    { id: "llm",       label: t("settings.tabLlm", { defaultValue: "LLM" }),            icon: Cpu,              component: LlmConfigPage },
    { id: "gitea",     label: t("settings.tabGitea", { defaultValue: "Gitea" }),         icon: GitBranch,        component: GiteaConfigPage },
    { id: "github",    label: t("settings.tabGithub", { defaultValue: "GitHub" }),       icon: Github,           component: GitHubConfigPage },
    { id: "vpn",       label: t("settings.tabVpn", { defaultValue: "VPN" }),             icon: Network,          component: VpnPage },
    { id: "kas",       label: t("settings.tabKas"),                                       icon: Mail,             component: KasConfigPage },
    { id: "backup",    label: t("settings.tabBackup", { defaultValue: "Backup" }),       icon: Archive,          component: BackupPage },
    { id: "migration", label: t("settings.tabMigration", { defaultValue: "Migration" }), icon: ArrowRightLeft,   component: MigrationPage },
  ], [t]);

  const ActiveComponent = TABS.find(tab => tab.id === active)!.component;

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <Settings size={20} className="text-muted-foreground" />
          <h1 className="text-lg font-semibold text-foreground">{t("settings.title")}</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4">{t("pageDesc.settings")}</p>
        <div className="mb-4 rounded-xl border bg-muted/30 p-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("settings.infoTitle")}</h3>
          <p className="text-xs text-muted-foreground leading-relaxed">{t("settings.infoText")}</p>
        </div>
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActive(tab.id)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px",
                active === tab.id
                  ? "border-primary text-foreground bg-background"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <tab.icon size={14} />
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        <ActiveComponent />
      </div>
    </div>
  );
}
