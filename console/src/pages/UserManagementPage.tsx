import { useState } from "react";
import { Users, Shield } from "lucide-react";
import { cn } from "@/lib/utils";
import { UserPage } from "@/pages/UserPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { useTranslation } from "react-i18next";

const TABS = [
  { id: "users",  label: "Benutzer", labelEn: "Users",  icon: Users },
  { id: "groups", label: "Gruppen",  labelEn: "Groups", icon: Shield },
] as const;

type TabId = typeof TABS[number]["id"];

export function UserManagementPage() {
  const { t, i18n } = useTranslation();
  const [active, setActive] = useState<TabId>("users");
  const isDE = i18n.language?.startsWith("de");

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 border-b border-zinc-800">
        <div className="flex items-center gap-2 mb-1">
          <Users size={20} className="text-zinc-400" />
          <h1 className="text-lg font-semibold text-zinc-100">Usermanagement</h1>
        </div>
        <p className="text-xs text-zinc-500 mb-4">{isDE ? "Benutzer anlegen, Gruppen verwalten und Berechtigungen zuweisen." : "Create users, manage groups and assign permissions."}</p>
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
              {isDE ? tab.label : tab.labelEn}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {active === "users" && <UserPage />}
        {active === "groups" && <GroupsPage />}
      </div>
    </div>
  );
}
