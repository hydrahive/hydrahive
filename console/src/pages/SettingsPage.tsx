import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Cpu, Plug, GitBranch, Network, Settings, Mail, Users, Archive } from "lucide-react";
import { cn } from "@/lib/utils";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import { McpConfigPage } from "@/pages/McpConfigPage";
import GiteaConfigPage from "@/pages/GiteaConfigPage";
import { VpnPage } from "@/pages/VpnPage";
import { KasConfigPage } from "@/pages/KasConfigPage";
import { UserPage } from "@/pages/UserPage";
import { BackupPage } from "@/pages/BackupPage";
import { useTranslation } from "react-i18next";

const TABS = [
  { id: "llm",    label: "LLM",        icon: Cpu,       component: LlmConfigPage },
  { id: "mcp",    label: "MCP",        icon: Plug,      component: McpConfigPage },
  { id: "gitea",  label: "Gitea",      icon: GitBranch, component: GiteaConfigPage },
  { id: "vpn",    label: "VPN",        icon: Network,   component: VpnPage },
  { id: "kas",    label: "Mail / KAS", icon: Mail,      component: KasConfigPage },
  { id: "users",  label: "Users",      icon: Users,     component: UserPage },
  { id: "backup", label: "Backup",     icon: Archive,   component: BackupPage },
] as const;

type TabId = typeof TABS[number]["id"];

const TAB_BY_HASH: Record<string, TabId> = {
  llm: "llm",
  mcp: "mcp",
  gitea: "gitea",
  vpn: "vpn",
  kas: "kas",
  users: "users",
  backup: "backup",
};

const DEFAULT_TAB: TabId = "llm";

export function SettingsPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const [active, setActive] = useState<TabId>(() => TAB_BY_HASH[location.hash.slice(1)] ?? DEFAULT_TAB);
  const ActiveComponent = TABS.find(t => t.id === active)!.component;

  useEffect(() => {
    setActive(TAB_BY_HASH[location.hash.slice(1)] ?? DEFAULT_TAB);
  }, [location.hash]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-zinc-800">
        <div className="flex items-center gap-2 mb-4">
          <Settings size={20} className="text-zinc-400" />
          <h1 className="text-lg font-semibold text-zinc-100">{t("settings.title")}</h1>
        </div>
        <div className="flex gap-1">
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
