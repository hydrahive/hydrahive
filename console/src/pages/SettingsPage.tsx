import { useMemo, useState } from "react";
import { Cpu, Plug, GitBranch, Github, Network, Settings, Mail, Users, Archive, ArrowRightLeft, Puzzle, Shield } from "lucide-react";
import { cn } from "@/lib/utils";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import { McpConfigPage } from "@/pages/McpConfigPage";
import GiteaConfigPage from "@/pages/GiteaConfigPage";
import { GitHubConfigPage } from "@/pages/GitHubConfigPage";
import { PluginsPage } from "@/pages/PluginsPage";
import { VpnPage } from "@/pages/VpnPage";
import { KasConfigPage } from "@/pages/KasConfigPage";
import { UserPage } from "@/pages/UserPage";
import { BackupPage } from "@/pages/BackupPage";
import { MigrationPage } from "@/pages/MigrationPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { useTranslation } from "react-i18next";

type TabId = "llm" | "mcp" | "gitea" | "github" | "vpn" | "kas" | "users" | "groups" | "backup" | "migration" | "plugins";

export function SettingsPage() {
  const { t } = useTranslation();
  const [active, setActive] = useState<TabId>("llm");

  const TABS: { id: TabId; label: string; icon: React.ElementType; component: React.ComponentType }[] = useMemo(() => [
    { id: "llm",       label: t("settings.tabLlm", { defaultValue: "LLM" }),        icon: Cpu,              component: LlmConfigPage },
    { id: "mcp",       label: t("settings.tabMcp", { defaultValue: "MCP" }),         icon: Plug,             component: McpConfigPage },
    { id: "gitea",     label: t("settings.tabGitea", { defaultValue: "Gitea" }),     icon: GitBranch,        component: GiteaConfigPage },
    { id: "github",    label: t("settings.tabGithub", { defaultValue: "GitHub" }),   icon: Github,           component: GitHubConfigPage },
    { id: "vpn",       label: t("settings.tabVpn", { defaultValue: "VPN" }),         icon: Network,          component: VpnPage },
    { id: "kas",       label: t("settings.tabKas"),      icon: Mail,             component: KasConfigPage },
    { id: "users",     label: t("settings.tabUsers"),    icon: Users,            component: UserPage },
    { id: "groups",    label: t("settings.tabGroups"),   icon: Shield,           component: GroupsPage },
    { id: "backup",    label: t("settings.tabBackup", { defaultValue: "Backup" }),   icon: Archive,          component: BackupPage },
    { id: "migration", label: t("settings.tabMigration", { defaultValue: "Migration" }), icon: ArrowRightLeft, component: MigrationPage },
    { id: "plugins",   label: t("settings.tabPlugins", { defaultValue: "Plugins" }), icon: Puzzle,           component: PluginsPage },
  ], [t]);

  const ActiveComponent = TABS.find(tab => tab.id === active)!.component;

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-zinc-800">
        <div className="flex items-center gap-2 mb-1">
          <Settings size={20} className="text-zinc-400" />
          <h1 className="text-lg font-semibold text-zinc-100">{t("settings.title")}</h1>
        </div>
        <p className="text-xs text-zinc-500 mb-4">{t("pageDesc.settings")}</p>
        <div className="flex gap-1 overflow-x-auto scrollbar-none pb-px">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActive(tab.id)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px",
                active === tab.id
                  ? "border-blue-500 text-zinc-100 bg-zinc-900"
                  : "border-transparent text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50"
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
