/**
 * RoleSelector — Agent-Rollen-Auswahl (#492)
 *
 * Zeigt 4 Rollen-Karten + Custom-Option.
 * Wiederverwendbar für MyAgentPage und AgentsPage.
 */
import { useState } from "react";
import { Eye, Bot, Code, Shield, Settings2, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface RoleInfo {
  id: string;
  label: string;
  desc: string;
  icon: React.ElementType;
  tools: string[] | "__ALL__";
  toolCount: number;
  danger?: boolean;
}

const ROLES: RoleInfo[] = [
  { id: "reader",    label: "Leser",       desc: "Lesen & Suchen",        icon: Eye,      tools: [], toolCount: 7 },
  { id: "assistant", label: "Assistent",    desc: "Lesen & Schreiben",     icon: Bot,      tools: [], toolCount: 13 },
  { id: "coder",     label: "Entwickler",   desc: "Shell, Git & Code",     icon: Code,     tools: [], toolCount: 27 },
  { id: "admin",     label: "Admin",        desc: "Vollzugriff",           icon: Shield,   tools: "__ALL__", toolCount: -1, danger: true },
];

interface RoleSelectorProps {
  value: string | null;
  onChange: (role: string | null) => void;
  /** Remote role data from /agent-roles API */
  roleData?: Record<string, { description: string; tools: string[] | string; tool_count: number }>;
  /** Show custom option (default: true) */
  showCustom?: boolean;
}

export function RoleSelector({ value, onChange, roleData, showCustom = true }: RoleSelectorProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<string | null>(null);

  const roles = ROLES.map(r => {
    if (roleData?.[r.id]) {
      const rd = roleData[r.id];
      return {
        ...r,
        desc: rd.description || r.desc,
        toolCount: rd.tool_count >= 0 ? rd.tool_count : r.toolCount,
        tools: Array.isArray(rd.tools) ? rd.tools : r.tools,
      };
    }
    return r;
  });

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {roles.map(role => {
          const Icon = role.icon;
          const active = value === role.id;
          return (
            <button
              key={role.id}
              type="button"
              onClick={() => onChange(role.id)}
              className={`flex flex-col items-center gap-1.5 rounded-xl border-2 px-3 py-3 text-center transition-all ${
                active
                  ? role.danger
                    ? "border-red-500 bg-red-500/10 text-red-500"
                    : "border-primary bg-primary/10 text-primary"
                  : "border-border/60 hover:border-primary/40 hover:bg-muted/50"
              }`}
            >
              <Icon className={`h-5 w-5 ${active ? "" : "text-muted-foreground"}`} />
              <span className="text-xs font-semibold">{role.label}</span>
              <span className="text-[10px] text-muted-foreground">{role.desc}</span>
              <span className={`text-[10px] font-mono ${active ? "opacity-80" : "text-muted-foreground/60"}`}>
                {role.toolCount < 0 ? "alle" : `${role.toolCount} Tools`}
              </span>
            </button>
          );
        })}
      </div>

      {/* Custom option */}
      {showCustom && (
        <button
          type="button"
          onClick={() => onChange(null)}
          className={`flex w-full items-center gap-2 rounded-xl border-2 px-3 py-2 text-left text-xs transition-all ${
            value === null
              ? "border-primary bg-primary/10 text-primary"
              : "border-border/60 hover:border-primary/40 hover:bg-muted/50 text-muted-foreground"
          }`}
        >
          <Settings2 className="h-4 w-4" />
          <span className="font-medium">Custom</span>
          <span className="text-[10px]">— Eigene Tool-Auswahl</span>
        </button>
      )}

      {/* Tool details expandable */}
      {value && value !== null && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded(expanded === value ? null : value)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronDown className={`h-3 w-3 transition-transform ${expanded === value ? "rotate-180" : ""}`} />
            Tools anzeigen
          </button>
          {expanded === value && (() => {
            const role = roles.find(r => r.id === value);
            if (!role || !Array.isArray(role.tools) || role.tools.length === 0) {
              return <p className="text-[11px] text-muted-foreground pl-4 py-1">Alle registrierten Tools</p>;
            }
            return (
              <div className="mt-1 flex flex-wrap gap-1 pl-4">
                {role.tools.map(t => (
                  <span key={t} className="rounded-md border bg-muted/50 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                    {t}
                  </span>
                ))}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
