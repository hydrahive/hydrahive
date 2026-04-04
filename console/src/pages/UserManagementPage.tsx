import { useState } from "react";
import type { ElementType } from "react";
import { Users, Shield, KeyRound, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { UserPage } from "@/pages/UserPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { SecretsPage } from "@/pages/SecretsPage";
import { PermissionsTab } from "@/pages/blueprint/PermissionsTab";
import { useTranslation } from "react-i18next";

const TABS: { id: "users" | "groups" | "secrets" | "permissions"; labelKey: string; icon: ElementType }[] = [
  { id: "users",       labelKey: "usermanagement.tabUsers",       icon: Users },
  { id: "groups",      labelKey: "usermanagement.tabGroups",      icon: Shield },
  { id: "secrets",     labelKey: "usermanagement.tabSecrets",     icon: KeyRound },
  { id: "permissions", labelKey: "usermanagement.tabPermissions", icon: Lock },
];

type TabId = typeof TABS[number]["id"];

export function UserManagementPage() {
  const { t } = useTranslation();
  const [active, setActive] = useState<TabId>("users");

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <Users size={20} className="text-muted-foreground" />
          <h1 className="text-lg font-semibold text-foreground">{t("usermanagement.title")}</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4">{t("usermanagement.subtitle")}</p>
        <div className="mb-4 rounded-xl border bg-muted/30 p-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("usermanagement.infoTitle")}</h3>
          <p className="text-xs text-muted-foreground leading-relaxed">{t("usermanagement.infoText")}</p>
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
              {t(tab.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {active === "users" && <UserPage />}
        {active === "groups" && <GroupsPage />}
        {active === "secrets" && <SecretsPage />}
        {active === "permissions" && <PermissionsTab />}
      </div>
    </div>
  );
}
