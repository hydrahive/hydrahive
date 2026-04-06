import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  Globe, Brain, Folder, Terminal, GitBranch, Server,
  Bot, Monitor, Mail, MessageCircle, BookOpen, Key,
  Puzzle, ChevronDown, ChevronRight, Wrench, ShieldOff,
} from "lucide-react";

/* ── Types ── */
interface ToolGroup {
  id: string;
  label: string;
  icon: string;
  tools: string[];
  unrestricted?: boolean;
}

export interface ToolGroupSelectorProps {
  selectedTools: string[];
  onChange: (tools: string[]) => void;
  onUnrestrictedChange?: (enabled: boolean) => void;
}

/* ── Icon map ── */
const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  globe: Globe,
  brain: Brain,
  folder: Folder,
  terminal: Terminal,
  "git-branch": GitBranch,
  gitbranch: GitBranch,
  server: Server,
  bot: Bot,
  monitor: Monitor,
  mail: Mail,
  messagecircle: MessageCircle,
  "message-circle": MessageCircle,
  bookopen: BookOpen,
  "book-open": BookOpen,
  key: Key,
  puzzle: Puzzle,
  "shield-off": ShieldOff,
  shieldoff: ShieldOff,
};

function GroupIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICON_MAP[name.toLowerCase()] ?? Wrench;
  return <Icon className={className} />;
}

/* ── Indeterminate checkbox helper ── */
function IndeterminateCheckbox({
  checked,
  indeterminate,
  onChange,
  className,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      className={className}
    />
  );
}

/* ── Main component ── */
export function ToolGroupSelector({ selectedTools, onChange, onUnrestrictedChange }: ToolGroupSelectorProps) {
  const [groups, setGroups] = useState<ToolGroup[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ groups: ToolGroup[] }>("/tool-groups")
      .then((d) => {
        setGroups(d.groups);
        setError("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Fehler beim Laden"))
      .finally(() => setLoading(false));
  }, []);

  const toggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleGroup = useCallback(
    (group: ToolGroup) => {
      const allSelected = group.tools.every((t) => selectedTools.includes(t));
      if (allSelected) {
        // Deselect all in group
        onChange(selectedTools.filter((t) => !group.tools.includes(t)));
        if (group.unrestricted && onUnrestrictedChange) onUnrestrictedChange(false);
      } else {
        // Select all in group
        const missing = group.tools.filter((t) => !selectedTools.includes(t));
        onChange([...selectedTools, ...missing]);
        if (group.unrestricted && onUnrestrictedChange) onUnrestrictedChange(true);
      }
    },
    [selectedTools, onChange, onUnrestrictedChange],
  );

  const toggleTool = useCallback(
    (tool: string) => {
      if (selectedTools.includes(tool)) {
        onChange(selectedTools.filter((t) => t !== tool));
      } else {
        onChange([...selectedTools, tool]);
      }
    },
    [selectedTools, onChange],
  );

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-10 animate-pulse rounded-lg bg-muted/40" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
        Tool-Groups konnten nicht geladen werden: {error}
      </p>
    );
  }

  if (groups.length === 0) return null;

  return (
    <div className="space-y-1 max-h-[300px] overflow-y-auto rounded-xl border border-border/60 bg-card p-1.5">
      {groups.map((group) => {
        const selectedCount = group.tools.filter((t) => selectedTools.includes(t)).length;
        const allSelected = selectedCount === group.tools.length;
        const someSelected = selectedCount > 0 && !allSelected;
        const isExpanded = expanded.has(group.id);

        return (
          <div key={group.id}>
            {/* Group row */}
            <div
              className={`flex items-center gap-2 rounded-lg px-3 py-2 transition-colors cursor-pointer select-none ${group.unrestricted && allSelected ? "bg-red-500/10 hover:bg-red-500/20 border border-red-500/30" : "hover:bg-muted"}`}
              onClick={() => toggleExpand(group.id)}
            >
              <IndeterminateCheckbox
                checked={allSelected}
                indeterminate={someSelected}
                onChange={(e) => {
                  e.stopPropagation();
                  toggleGroup(group);
                }}
                className="h-4 w-4 rounded accent-primary flex-shrink-0"
              />
              <GroupIcon name={group.icon} className="h-4 w-4 text-muted-foreground flex-shrink-0" />
              <span className={`flex-1 text-sm font-medium truncate ${group.unrestricted && allSelected ? "text-red-600 dark:text-red-400" : "text-foreground"}`}>
                {group.label}
              </span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground tabular-nums">
                {selectedCount}/{group.tools.length}
              </span>
              {isExpanded ? (
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
              )}
            </div>

            {/* Expanded: individual tools */}
            {isExpanded && (
              <div className="grid grid-cols-2 gap-x-2 gap-y-1 px-3 pb-2 pt-1 md:grid-cols-3">
                {group.tools.map((tool) => (
                  <label
                    key={tool}
                    className="flex items-center gap-2 rounded-md px-2 py-1 text-xs cursor-pointer transition-colors hover:bg-muted/60"
                  >
                    <input
                      type="checkbox"
                      checked={selectedTools.includes(tool)}
                      onChange={() => toggleTool(tool)}
                      className="h-3.5 w-3.5 rounded accent-primary"
                    />
                    <span className="text-muted-foreground font-mono truncate">{tool}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
