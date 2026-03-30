import "@xyflow/react/dist/style.css";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  BackgroundVariant,
  Panel,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import {
  Workflow,
  Zap,
  Clock,
  Calendar,
  Users,
  MessageCircle,
  Bot,
  Inbox,
  EyeOff,
  ArrowRight,
  Plus,
  Save,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Filter,
  Webhook,
  Copy,
  Check,
  GitBranch,
  Globe,
  Mail,
  GitPullRequest,
  MessageSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

// ── Types ──────────────────────────────────────────────────────────────────
interface ButlerNodeData {
  subtype: string;
  label: string;
  params: Record<string, unknown>;
  [key: string]: unknown; // React Flow requires index signature
}

interface ButlerFlow {
  id: string;
  name: string;
  enabled: boolean;
  nodes: Node<ButlerNodeData>[];
  edges: Edge[];
}

type BNode = Node<ButlerNodeData>;

// ── Default params per subtype ─────────────────────────────────────────────
function defaultParams(subtype: string): Record<string, unknown> {
  switch (subtype) {
    case "message_received":       return { channel: "all" };
    case "webhook_received":       return { hook_id: "" };
    case "heartbeat_fired":        return { agent_id: "all", task_id: "" };
    case "git_event_received":     return { git_event: "push", channel: "both", repo: "" };
    case "time_window":            return { from: "23:00", to: "08:00" };
    case "day_of_week":            return { days: ["mo","di","mi","do","fr","sa","so"] };
    case "contact_known":          return {};
    case "message_contains":       return { keyword: "" };
    case "payload_field_contains": return { field: "", value: "" };
    case "git_branch_is":          return { branch: "" };
    case "git_author_is":          return { author: "" };
    case "git_action_is":          return { action: "opened" };
    case "agent_reply":            return { agent_id: "" };
    case "agent_reply_guided":     return { agent_id: "", instruction: "" };
    case "reply_fixed":            return { text: "" };
    case "queue":                  return {};
    case "ignore":                 return {};
    case "forward":                return { agent_id: "" };
    case "http_post":              return { url: "", headers: {}, body_template: "{}" };
    case "send_email":             return { to: "", subject: "", body: "" };
    case "git_create_issue":       return { repo: "", title: "", body: "" };
    case "git_add_comment":        return { repo: "", issue_number: "", body: "" };
    case "discord_post":           return { channel_id: "", message: "" };
    case "discord_event_received": return { discord_event: "reaction_add", channel_id: "" };
    case "email_received":         return { folder: "INBOX", from_filter: "" };
    case "email_from_contains":    return { keyword: "" };
    case "email_subject_contains": return { keyword: "" };
    case "email_body_contains":    return { keyword: "" };
    case "discord_event_is":       return { discord_event: "reaction_add" };
    case "discord_emoji_is":       return { emoji: "" };
    default:                       return {};
  }
}

// ── Palette definitions ────────────────────────────────────────────────────
const PALETTE = [
  {
    group: "Trigger",
    color: "green" as const,
    items: [
      { type: "triggerNode", subtype: "message_received",       label: "Nachricht empfangen", icon: MessageCircle },
      { type: "triggerNode", subtype: "webhook_received",       label: "Webhook empfangen",   icon: Webhook },
      { type: "triggerNode", subtype: "heartbeat_fired",        label: "Heartbeat Task",       icon: Clock },
      { type: "triggerNode", subtype: "git_event_received",     label: "GitHub/Gitea Event",   icon: GitBranch },
      { type: "triggerNode", subtype: "discord_event_received", label: "Discord Event",        icon: MessageSquare },
      { type: "triggerNode", subtype: "email_received",         label: "E-Mail empfangen",     icon: Mail },
    ],
  },
  {
    group: "Bedingung",
    color: "blue" as const,
    items: [
      { type: "conditionNode", subtype: "time_window",            label: "Zeitfenster",         icon: Clock },
      { type: "conditionNode", subtype: "day_of_week",            label: "Wochentag",           icon: Calendar },
      { type: "conditionNode", subtype: "contact_known",          label: "Kontakt bekannt?",    icon: Users },
      { type: "conditionNode", subtype: "message_contains",       label: "Text enthält",        icon: Filter },
      { type: "conditionNode", subtype: "payload_field_contains", label: "Payload-Feld enthält",icon: Filter },
      { type: "conditionNode", subtype: "git_branch_is",          label: "Branch ist",          icon: GitBranch },
      { type: "conditionNode", subtype: "git_author_is",          label: "Autor ist",           icon: Users },
      { type: "conditionNode", subtype: "git_action_is",          label: "Git-Action ist",      icon: Zap },
      { type: "conditionNode", subtype: "email_from_contains",    label: "Mail-Absender enthält",icon: Mail },
      { type: "conditionNode", subtype: "email_subject_contains", label: "Mail-Betreff enthält", icon: Mail },
      { type: "conditionNode", subtype: "email_body_contains",    label: "Mail-Text enthält",    icon: Mail },
      { type: "conditionNode", subtype: "discord_event_is",       label: "Discord-Event ist",    icon: MessageSquare },
      { type: "conditionNode", subtype: "discord_emoji_is",       label: "Discord-Emoji ist",    icon: MessageSquare },
    ],
  },
  {
    group: "Aktion",
    color: "orange" as const,
    items: [
      { type: "actionNode", subtype: "agent_reply",         label: "Agent antwortet",    icon: Bot },
      { type: "actionNode", subtype: "agent_reply_guided",  label: "Agent mit Vorgabe",  icon: MessageCircle },
      { type: "actionNode", subtype: "reply_fixed",         label: "Feste Antwort",      icon: Zap },
      { type: "actionNode", subtype: "queue",               label: "In Warteschlange",   icon: Inbox },
      { type: "actionNode", subtype: "ignore",              label: "Ignorieren",         icon: EyeOff },
      { type: "actionNode", subtype: "forward",             label: "Weiterleiten",       icon: ArrowRight },
      { type: "actionNode", subtype: "http_post",           label: "HTTP POST",          icon: Globe },
      { type: "actionNode", subtype: "send_email",          label: "E-Mail senden",      icon: Mail },
      { type: "actionNode", subtype: "git_create_issue",    label: "Gitea: Issue",       icon: GitPullRequest },
      { type: "actionNode", subtype: "git_add_comment",     label: "Gitea: Kommentar",   icon: GitBranch },
      { type: "actionNode", subtype: "discord_post",        label: "Discord: Post",      icon: MessageSquare },
    ],
  },
];

// ── Summary text for node preview ─────────────────────────────────────────
function paramSummary(subtype: string, params: Record<string, unknown>): string {
  switch (subtype) {
    case "message_received": {
      const ch = (params.channel as string) || "all";
      return ch === "all" ? "Alle Kanäle" : ch.charAt(0).toUpperCase() + ch.slice(1);
    }
    case "webhook_received":
      return (params.hook_id as string) ? `/${params.hook_id}` : "— hook_id fehlt —";
    case "heartbeat_fired": {
      const agent = (params.agent_id as string) || "all";
      const task  = (params.task_id as string) || "";
      return agent === "all" ? (task ? `Alle Agenten · ${task}` : "Alle Agenten") : (task ? `${agent} · ${task}` : agent);
    }
    case "git_event_received": {
      const evt = (params.git_event as string) || "push";
      const ch  = (params.channel as string) || "both";
      const repo = (params.repo as string) || "";
      return `${ch === "both" ? "GitHub/Gitea" : ch} · ${evt}${repo ? ` · ${repo}` : ""}`;
    }
    case "payload_field_contains":
      return params.field ? `${params.field} ≈ "${params.value}"` : "—";
    case "git_branch_is":
      return (params.branch as string) || "—";
    case "git_author_is":
      return (params.author as string) || "—";
    case "git_action_is":
      return (params.action as string) || "—";
    case "time_window":
      return `${params.from ?? "?"}–${params.to ?? "?"}`;
    case "day_of_week": {
      const days = (params.days as string[]) ?? [];
      return days.map(d => d.charAt(0).toUpperCase() + d.slice(1)).join(" ");
    }
    case "message_contains":
      return params.keyword ? `"${params.keyword}"` : "—";
    case "agent_reply":
    case "forward":
      return (params.agent_id as string) || "—";
    case "agent_reply_guided":
      return (params.instruction as string)?.slice(0, 30) || "—";
    case "reply_fixed":
      return (params.text as string)?.slice(0, 30) || "—";
    case "http_post":
      return (params.url as string)?.slice(0, 35) || "— url fehlt —";
    case "send_email":
      return (params.to as string) || "— to fehlt —";
    case "git_create_issue":
      return (params.repo as string) ? `${params.repo}: ${(params.title as string)?.slice(0, 20) || ""}` : "— repo fehlt —";
    case "git_add_comment":
      return (params.repo as string) ? `${params.repo} #${params.issue_number || "?"}` : "— repo fehlt —";
    case "discord_post":
      return (params.channel_id as string) ? `#${params.channel_id}` : "— channel fehlt —";
    case "discord_event_received": {
      const evt = (params.discord_event as string) || "reaction_add";
      const ch  = (params.channel_id as string) || "";
      return ch ? `${evt} · #${ch}` : evt;
    }
    case "email_received": {
      const folder = (params.folder as string) || "INBOX";
      const from   = (params.from_filter as string) || "";
      return from ? `${folder} · von: ${from}` : folder;
    }
    case "email_from_contains":
    case "email_subject_contains":
    case "email_body_contains":
      return (params.keyword as string) ? `"${params.keyword}"` : "—";
    case "discord_event_is":
      return (params.discord_event as string) || "—";
    case "discord_emoji_is":
      return (params.emoji as string) || "—";
    default:
      return "";
  }
}

// ── Custom node components ─────────────────────────────────────────────────
function TriggerNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-green-500/60 bg-green-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <div className="flex items-center gap-1.5 mb-1">
        <Zap className="h-3 w-3 text-green-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-green-400">Trigger</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-green-300/60 mt-0.5">{summary}</p>}
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        style={{ background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
      />
    </div>
  );
}

function ConditionNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-blue-500/60 bg-blue-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        style={{ background: "#3b82f6", border: "2px solid #1d4ed8", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Bedingung</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-blue-300/60 mt-0.5">{summary}</p>}
      {/* true / false output handles */}
      <div className="relative mt-2 h-8">
        <Handle
          type="source"
          position={Position.Right}
          id="true"
          style={{ top: "25%", background: "#22c55e", border: "2px solid #16a34a", width: 10, height: 10 }}
        />
        <span className="absolute right-[-22px] top-[0px] text-[9px] text-green-400 font-semibold leading-none">ja</span>
        <Handle
          type="source"
          position={Position.Right}
          id="false"
          style={{ top: "75%", background: "#ef4444", border: "2px solid #b91c1c", width: 10, height: 10 }}
        />
        <span className="absolute right-[-24px] bottom-[0px] text-[9px] text-red-400 font-semibold leading-none">nein</span>
      </div>
    </div>
  );
}

function ActionNodeComp({ data, selected }: { data: ButlerNodeData; selected: boolean }) {
  const summary = paramSummary(data.subtype, data.params);
  return (
    <div className={cn(
      "min-w-[185px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none",
      "border-orange-500/60 bg-orange-950/50",
      selected && "ring-2 ring-white/25"
    )}>
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        style={{ background: "#f97316", border: "2px solid #c2410c", width: 10, height: 10 }}
      />
      <div className="flex items-center gap-1.5 mb-1">
        <Zap className="h-3 w-3 text-orange-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-orange-400">Aktion</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label}</p>
      {summary && <p className="text-xs text-orange-300/60 mt-0.5">{summary}</p>}
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        style={{ background: "#f97316", border: "2px solid #c2410c", width: 10, height: 10 }}
      />
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const NODE_TYPES: NodeTypes = {
  triggerNode:   TriggerNodeComp as any,
  conditionNode: ConditionNodeComp as any,
  actionNode:    ActionNodeComp as any,
};

// ── Node Palette (left sidebar) ────────────────────────────────────────────
function NodePalette() {
  const [open, setOpen] = React.useState<Record<string, boolean>>({ Trigger: true, Bedingung: false, Aktion: false });

  const onDragStart = (event: React.DragEvent, item: { type: string; subtype: string; label: string }) => {
    event.dataTransfer.setData("application/butler-node", JSON.stringify(item));
    event.dataTransfer.effectAllowed = "move";
  };

  const colorMap = {
    green:  "border-green-500/40 bg-green-950/30 hover:bg-green-950/60 text-green-300",
    blue:   "border-blue-500/40 bg-blue-950/30 hover:bg-blue-950/60 text-blue-300",
    orange: "border-orange-500/40 bg-orange-950/30 hover:bg-orange-950/60 text-orange-300",
  };
  const headerColor = {
    green:  "text-green-400/70 hover:text-green-300",
    blue:   "text-blue-400/70 hover:text-blue-300",
    orange: "text-orange-400/70 hover:text-orange-300",
  };

  return (
    <div className="w-44 shrink-0 overflow-y-auto border-r border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] p-3 flex flex-col gap-2">
      <p className="text-[0.6rem] font-semibold uppercase tracking-[0.18em] text-white/30 px-1 mb-1">Knoten-Palette</p>
      {PALETTE.map(group => {
        const isOpen = open[group.group] ?? true;
        return (
          <div key={group.group}>
            <button
              type="button"
              onClick={() => setOpen(prev => ({ ...prev, [group.group]: !isOpen }))}
              className={cn(
                "w-full flex items-center justify-between px-1 py-1 text-[0.55rem] font-bold uppercase tracking-widest transition-colors",
                headerColor[group.color]
              )}
            >
              <span>{group.group}</span>
              <span className="text-white/20">{isOpen ? "▲" : "▼"}</span>
            </button>
            {isOpen && (
              <div className="flex flex-col gap-1.5 mt-1">
                {group.items.map(item => {
                  const Icon = item.icon;
                  return (
                    <div
                      key={item.subtype}
                      className={cn(
                        "flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs",
                        "cursor-grab active:cursor-grabbing transition-colors",
                        colorMap[group.color]
                      )}
                      draggable
                      onDragStart={e => onDragStart(e, item)}
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate leading-tight">{item.label}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      <div className="mt-auto pt-3 border-t border-white/10">
        <p className="text-[0.55rem] text-white/20 leading-relaxed px-1">
          Knoten auf die Canvas ziehen, dann verbinden und speichern.
        </p>
      </div>
    </div>
  );
}

// ── Properties Panel (right sidebar) ──────────────────────────────────────
interface PropsPanelProps {
  node: BNode;
  agents: { id: string; name: string }[];
  onChange: (params: Record<string, unknown>) => void;
  onDelete: () => void;
}

function PropertiesPanel({ node, agents, onChange, onDelete }: PropsPanelProps) {
  const d = node.data;
  const p = d.params;
  const ALL_DAYS = ["mo","di","mi","do","fr","sa","so"];
  const DAY_LABEL: Record<string, string> = { mo:"Mo",di:"Di",mi:"Mi",do:"Do",fr:"Fr",sa:"Sa",so:"So" };

  return (
    <div className="w-56 shrink-0 border-l border-white/10 bg-[hsl(var(--sidebar-bg,220_15%_8%))] p-4 flex flex-col gap-4 overflow-y-auto">
      <div>
        <p className="text-[0.55rem] font-bold uppercase tracking-widest text-white/30 mb-1">Eigenschaften</p>
        <p className="text-sm font-semibold text-white">{d.label}</p>
      </div>

      {/* Trigger: Webhook empfangen */}
      {d.subtype === "webhook_received" && (
        <WebhookTriggerPanel params={p} onChange={onChange} />
      )}

      {/* Trigger: GitHub/Gitea Event */}
      {d.subtype === "git_event_received" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Dienst</label>
            <select value={(p.channel as string) || "both"}
              onChange={e => onChange({ ...p, channel: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
              <option value="both">GitHub + Gitea</option>
              <option value="github">GitHub</option>
              <option value="gitea">Gitea</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Event-Typ</label>
            <select value={(p.git_event as string) || "push"}
              onChange={e => onChange({ ...p, git_event: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
              <option value="push">Push</option>
              <option value="pull_request">Pull Request</option>
              <option value="issues">Issue</option>
              <option value="issue_comment">Issue Kommentar</option>
              <option value="release">Release</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Repository-Filter (optional)</label>
            <input type="text" placeholder="z.B. mein-org/mein-repo"
              value={(p.repo as string) || ""}
              onChange={e => onChange({ ...p, repo: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
            <p className="text-[10px] text-white/25 mt-1">Leer = alle Repos.</p>
          </div>
          <div className="border-t border-white/10 pt-2">
            <p className="text-[10px] text-white/40 leading-relaxed">
              <strong className="text-white/60">Webhook-URLs:</strong><br />
              GitHub: <code className="text-cyan-400">/webhooks/github</code><br />
              Gitea: <code className="text-cyan-400">/webhooks/gitea-butler</code>
            </p>
          </div>
        </div>
      )}

      {/* Trigger: Heartbeat Task */}
      {d.subtype === "heartbeat_fired" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Agent</label>
            <select value={(p.agent_id as string) || "all"}
              onChange={e => onChange({ ...p, agent_id: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
              <option value="all">Alle Agenten</option>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Task-ID (optional)</label>
            <input type="text" placeholder="z.B. daily-report"
              value={(p.task_id as string) || ""}
              onChange={e => onChange({ ...p, task_id: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
            <p className="text-[10px] text-white/25 mt-1">Leer = alle Heartbeat-Tasks des Agenten.</p>
          </div>
        </div>
      )}

      {/* Trigger: Nachricht empfangen */}
      {d.subtype === "message_received" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Kanal</label>
          <select
            value={(p.channel as string) || "all"}
            onChange={e => onChange({ ...p, channel: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
          >
            <option value="all">Alle Kanäle</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="telegram">Telegram</option>
            <option value="discord">Discord</option>
            <option value="matrix">Matrix</option>
          </select>
        </div>
      )}

      {/* Condition: Zeitfenster */}
      {d.subtype === "time_window" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Von</label>
            <input type="time" value={(p.from as string) || "23:00"}
              onChange={e => onChange({ ...p, from: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Bis</label>
            <input type="time" value={(p.to as string) || "08:00"}
              onChange={e => onChange({ ...p, to: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
            />
          </div>
          <p className="text-[10px] text-white/25">Übernacht (23:00–08:00) wird unterstützt.</p>
        </div>
      )}

      {/* Condition: Wochentag */}
      {d.subtype === "day_of_week" && (
        <div>
          <label className="block text-xs text-white/50 mb-2">Tage</label>
          <div className="flex flex-wrap gap-1">
            {ALL_DAYS.map(day => {
              const days = (p.days as string[]) || ALL_DAYS;
              const active = days.includes(day);
              return (
                <button key={day} type="button"
                  onClick={() => {
                    const next = active ? days.filter(d => d !== day) : [...days, day];
                    onChange({ ...p, days: next });
                  }}
                  className={cn(
                    "w-8 py-1 rounded text-xs font-medium transition-colors",
                    active ? "bg-blue-600 text-white" : "bg-zinc-900 text-white/35 hover:bg-white/10"
                  )}
                >
                  {DAY_LABEL[day]}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Condition: Text enthält */}
      {d.subtype === "message_contains" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Stichwort</label>
          <input type="text" placeholder="z.B. dringend"
            value={(p.keyword as string) || ""}
            onChange={e => onChange({ ...p, keyword: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
        </div>
      )}

      {/* Condition: Payload-Feld enthält */}
      {d.subtype === "payload_field_contains" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Feld (Punkt-Notation)</label>
            <input type="text" placeholder="z.B. action oder pull_request.state"
              value={(p.field as string) || ""}
              onChange={e => onChange({ ...p, field: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Wert enthält</label>
            <input type="text" placeholder="z.B. opened"
              value={(p.value as string) || ""}
              onChange={e => onChange({ ...p, value: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <p className="text-[10px] text-white/25">Groß-/Kleinschreibung wird ignoriert.</p>
        </div>
      )}

      {/* Action: Agent antwortet / Weiterleiten / Mit Vorgabe */}
      {(d.subtype === "agent_reply" || d.subtype === "forward" || d.subtype === "agent_reply_guided") && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Agent</label>
          <select value={(p.agent_id as string) || ""}
            onChange={e => onChange({ ...p, agent_id: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30"
          >
            <option value="">— wählen —</option>
            {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      )}

      {/* Action: Agent mit Vorgabe — Instruktion */}
      {d.subtype === "agent_reply_guided" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Vorgabe / Instruktion</label>
          <textarea
            rows={3}
            placeholder="z.B. Antworte kurz und formell auf Deutsch"
            value={(p.instruction as string) || ""}
            onChange={e => onChange({ ...p, instruction: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
          />
          <p className="text-[10px] text-white/25 mt-1">Wird dem Agent als Vorgabe vor der Nachricht übergeben.</p>
        </div>
      )}

      {/* Action: Feste Antwort */}
      {d.subtype === "reply_fixed" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Antworttext</label>
          <textarea
            rows={4}
            placeholder="z.B. Ich bin gerade nicht erreichbar. Melde mich morgen."
            value={(p.text as string) || ""}
            onChange={e => onChange({ ...p, text: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
          />
          <p className="text-[10px] text-white/25 mt-1">Wird direkt gesendet — kein LLM, sofort.</p>
        </div>
      )}

      {/* Condition: Branch ist */}
      {d.subtype === "git_branch_is" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Branch-Name</label>
          <input type="text" placeholder="z.B. main"
            value={(p.branch as string) || ""}
            onChange={e => onChange({ ...p, branch: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
        </div>
      )}

      {/* Condition: Autor ist */}
      {d.subtype === "git_author_is" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">GitHub/Gitea Username</label>
          <input type="text" placeholder="z.B. tilleulenspiegel"
            value={(p.author as string) || ""}
            onChange={e => onChange({ ...p, author: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
          <p className="text-[10px] text-white/25 mt-1">Groß-/Kleinschreibung wird ignoriert.</p>
        </div>
      )}

      {/* Condition: Git-Action ist */}
      {d.subtype === "git_action_is" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Action</label>
          <select value={(p.action as string) || "opened"}
            onChange={e => onChange({ ...p, action: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
            <option value="opened">opened</option>
            <option value="closed">closed</option>
            <option value="merged">merged (PR)</option>
            <option value="reopened">reopened</option>
            <option value="labeled">labeled</option>
            <option value="created">created (Kommentar)</option>
            <option value="published">published (Release)</option>
          </select>
        </div>
      )}

      {/* Trigger: Discord Event */}
      {d.subtype === "discord_event_received" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Event-Typ</label>
            <select value={(p.discord_event as string) || "reaction_add"}
              onChange={e => onChange({ ...p, discord_event: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
              <option value="reaction_add">Reaktion hinzugefügt</option>
              <option value="reaction_remove">Reaktion entfernt</option>
              <option value="member_join">Mitglied beigetreten</option>
              <option value="member_remove">Mitglied entfernt</option>
              <option value="channel_create">Channel erstellt</option>
              <option value="channel_delete">Channel gelöscht</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Channel-ID (optional)</label>
            <input type="text" placeholder="Leer = alle Channels"
              value={(p.channel_id as string) || ""}
              onChange={e => onChange({ ...p, channel_id: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm font-mono text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
            <p className="text-[10px] text-white/25 mt-1">Discord Channel-ID (Rechtsklick → ID kopieren)</p>
          </div>
        </div>
      )}

      {/* Trigger: E-Mail empfangen */}
      {d.subtype === "email_received" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">IMAP-Ordner</label>
            <input type="text" placeholder="INBOX"
              value={(p.folder as string) || "INBOX"}
              onChange={e => onChange({ ...p, folder: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Absender-Filter (optional)</label>
            <input type="text" placeholder="z.B. @example.com"
              value={(p.from_filter as string) || ""}
              onChange={e => onChange({ ...p, from_filter: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
            <p className="text-[10px] text-white/25 mt-1">Leer = alle Absender. Konfiguration in /etc/hydrahive/kas.json</p>
          </div>
        </div>
      )}

      {/* Condition: E-Mail Felder */}
      {(d.subtype === "email_from_contains" || d.subtype === "email_subject_contains" || d.subtype === "email_body_contains") && (
        <div>
          <label className="block text-xs text-white/50 mb-1">
            {d.subtype === "email_from_contains" ? "Absender enthält" :
             d.subtype === "email_subject_contains" ? "Betreff enthält" : "Text enthält"}
          </label>
          <input type="text" placeholder="Stichwort oder Domain"
            value={(p.keyword as string) || ""}
            onChange={e => onChange({ ...p, keyword: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
          <p className="text-[10px] text-white/25 mt-1">Groß-/Kleinschreibung wird ignoriert.</p>
        </div>
      )}

      {/* Condition: Discord Event ist */}
      {d.subtype === "discord_event_is" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Event-Typ</label>
          <select value={(p.discord_event as string) || "reaction_add"}
            onChange={e => onChange({ ...p, discord_event: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white focus:outline-none focus:border-white/30">
            <option value="reaction_add">Reaktion hinzugefügt</option>
            <option value="reaction_remove">Reaktion entfernt</option>
            <option value="member_join">Mitglied beigetreten</option>
            <option value="member_remove">Mitglied entfernt</option>
            <option value="channel_create">Channel erstellt</option>
            <option value="channel_delete">Channel gelöscht</option>
          </select>
        </div>
      )}

      {/* Condition: Discord Emoji ist */}
      {d.subtype === "discord_emoji_is" && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Emoji</label>
          <input type="text" placeholder="👍 oder custom_emoji_name"
            value={(p.emoji as string) || ""}
            onChange={e => onChange({ ...p, emoji: e.target.value })}
            className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
          />
          <p className="text-[10px] text-white/25 mt-1">Unicode-Emoji oder Name eines Custom-Emojis.</p>
        </div>
      )}

      {/* Info-only nodes */}
      {d.subtype === "contact_known" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Prüft ob der Absender in der HydraHive-Kontaktliste eingetragen ist.
        </p>
      )}
      {d.subtype === "ignore" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Nachricht wird still ignoriert — keine Antwort, kein Logging.
        </p>
      )}
      {d.subtype === "queue" && (
        <p className="text-xs text-white/35 leading-relaxed">
          Nachricht wird in der Warteschlange für spätere Bearbeitung gespeichert.
        </p>
      )}

      {/* Action: HTTP POST */}
      {d.subtype === "http_post" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Ziel-URL</label>
            <input type="text" placeholder="https://example.com/webhook"
              value={(p.url as string) || ""}
              onChange={e => onChange({ ...p, url: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Body (JSON-Template)</label>
            <textarea rows={4} placeholder={`{\n  "text": "{{event.message_text}}"\n}`}
              value={(p.body_template as string) || "{}"}
              onChange={e => onChange({ ...p, body_template: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-xs font-mono text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
            />
          </div>
          <p className="text-[10px] text-white/25">Platzhalter: <code className="text-cyan-400">{"{{event.message_text}}"}</code>, <code className="text-cyan-400">{"{{event.extra.repo}}"}</code> etc.</p>
        </div>
      )}

      {/* Action: E-Mail senden */}
      {d.subtype === "send_email" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">An (Empfänger)</label>
            <input type="text" placeholder="empfaenger@example.com oder {{event.extra.from}}"
              value={(p.to as string) || ""}
              onChange={e => onChange({ ...p, to: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Betreff</label>
            <input type="text" placeholder="Neue Nachricht von {{event.contact_name}}"
              value={(p.subject as string) || ""}
              onChange={e => onChange({ ...p, subject: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Text</label>
            <textarea rows={3} placeholder="{{event.message_text}}"
              value={(p.body as string) || ""}
              onChange={e => onChange({ ...p, body: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
            />
          </div>
          <p className="text-[10px] text-white/25">Nutzt SMTP aus /etc/hydrahive/kas.json</p>
        </div>
      )}

      {/* Action: Gitea Issue erstellen */}
      {d.subtype === "git_create_issue" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Repository (owner/repo)</label>
            <input type="text" placeholder="hydrahive/hydrahive oder {{event.extra.repo}}"
              value={(p.repo as string) || ""}
              onChange={e => onChange({ ...p, repo: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Titel</label>
            <input type="text" placeholder="Bug: {{event.extra.commit_message}}"
              value={(p.title as string) || ""}
              onChange={e => onChange({ ...p, title: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Beschreibung (optional)</label>
            <textarea rows={3} placeholder="Ausgelöst durch: {{event.extra.author}}"
              value={(p.body as string) || ""}
              onChange={e => onChange({ ...p, body: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
            />
          </div>
        </div>
      )}

      {/* Action: Gitea Kommentar */}
      {d.subtype === "git_add_comment" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Repository (owner/repo)</label>
            <input type="text" placeholder="hydrahive/hydrahive"
              value={(p.repo as string) || ""}
              onChange={e => onChange({ ...p, repo: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Issue-Nummer</label>
            <input type="text" placeholder="42 oder {{event.extra.pr_number}}"
              value={(p.issue_number as string) || ""}
              onChange={e => onChange({ ...p, issue_number: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Kommentar</label>
            <textarea rows={3} placeholder="Automatisch von Butler via {{event.channel}}"
              value={(p.body as string) || ""}
              onChange={e => onChange({ ...p, body: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
            />
          </div>
        </div>
      )}

      {/* Action: Discord Post */}
      {d.subtype === "discord_post" && (
        <div className="flex flex-col gap-2">
          <div>
            <label className="block text-xs text-white/50 mb-1">Channel ID</label>
            <input type="text" placeholder="1234567890123456789"
              value={(p.channel_id as string) || ""}
              onChange={e => onChange({ ...p, channel_id: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm font-mono text-white placeholder-white/20 focus:outline-none focus:border-white/30"
            />
            <p className="text-[10px] text-white/25 mt-1">Discord Channel-ID (Rechtsklick → ID kopieren)</p>
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1">Nachricht</label>
            <textarea rows={3} placeholder="{{event.message_text}}"
              value={(p.message as string) || ""}
              onChange={e => onChange({ ...p, message: e.target.value })}
              className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30 resize-none"
            />
          </div>
        </div>
      )}

      <div className="mt-auto pt-3 border-t border-white/10">
        <button type="button" onClick={onDelete}
          className="flex items-center gap-1.5 text-xs text-red-400/60 hover:text-red-400 transition-colors"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Knoten entfernen
        </button>
      </div>
    </div>
  );
}

// ── Webhook Trigger Panel ─────────────────────────────────────────────────
function WebhookTriggerPanel({
  params,
  onChange,
}: {
  params: Record<string, unknown>;
  onChange: (p: Record<string, unknown>) => void;
}) {
  const [copied, setCopied] = useState(false);
  const hookId = (params.hook_id as string) || "";
  const baseUrl = typeof window !== "undefined" ? window.location.origin : "";
  const webhookUrl = hookId ? `${baseUrl}/webhooks/butler/${hookId}` : "";

  const copyUrl = () => {
    if (!webhookUrl) return;
    navigator.clipboard.writeText(webhookUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="block text-xs text-white/50 mb-1">Hook-ID</label>
        <input
          type="text"
          placeholder="z.B. github-push"
          value={hookId}
          onChange={e => onChange({ ...params, hook_id: e.target.value.replace(/[^a-z0-9_-]/gi, "-").toLowerCase() })}
          className="w-full rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-sm text-white placeholder-white/20 focus:outline-none focus:border-white/30"
        />
        <p className="text-[10px] text-white/25 mt-1">Nur Buchstaben, Zahlen, Bindestriche.</p>
      </div>

      {webhookUrl && (
        <div>
          <label className="block text-xs text-white/50 mb-1">Webhook-URL</label>
          <div className="flex items-center gap-1">
            <code className="flex-1 truncate rounded-lg bg-zinc-900 border border-white/15 px-2 py-1.5 text-[11px] text-cyan-300">
              {webhookUrl}
            </code>
            <button
              type="button"
              onClick={copyUrl}
              className="shrink-0 p-1.5 rounded-lg bg-zinc-900 border border-white/15 hover:bg-white/10 transition-colors"
              title="URL kopieren"
            >
              {copied
                ? <Check className="h-3.5 w-3.5 text-green-400" />
                : <Copy className="h-3.5 w-3.5 text-white/40" />}
            </button>
          </div>
          <p className="text-[10px] text-white/25 mt-1">
            POST an diese URL um den Flow auszulösen.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────
export function ButlerPage() {
  return (
    <ReactFlowProvider>
      <ButlerPageInner />
    </ReactFlowProvider>
  );
}

let _nSeq = 0;
function genId(type: string) { return `${type}-${++_nSeq}-${Date.now()}`; }

function ButlerPageInner() {
  const { t } = useTranslation();
  const [flows, setFlows]           = useState<ButlerFlow[]>([]);
  const [activeFlowId, setActiveId] = useState<string | null>(null);
  const [flowName, setFlowName]     = useState("Neuer Flow");
  const [flowEnabled, setEnabled]   = useState(true);
  const [saving, setSaving]         = useState(false);
  const [toast, setToast]           = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<BNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId]     = useState<string | null>(null);

  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const rf = useReactFlow();

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  // Load flows + agents
  useEffect(() => {
    api.get<ButlerFlow[]>("/butler/flows")
      .then(setFlows)
      .catch(() => {});

    api.get<Record<string, { config: { identity?: string } }>>("/agents")
      .then(res => {
        const list = Object.entries(res || {}).map(([id, a]) => ({
          id,
          name: a?.config?.identity ? `${id} — ${String(a.config.identity).slice(0, 30)}` : id,
        }));
        setAgents(list);
      })
      .catch(() => {});
  }, []);

  const loadFlow = (flow: ButlerFlow) => {
    setActiveId(flow.id);
    setFlowName(flow.name);
    setEnabled(flow.enabled);
    setNodes((flow.nodes || []) as BNode[]);
    setEdges(flow.edges || []);
    setSelectedId(null);
  };

  const newFlow = () => {
    setActiveId(null);
    setFlowName("Neuer Flow");
    setEnabled(true);
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
  };

  const saveFlow = async () => {
    setSaving(true);
    try {
      const payload = {
        name: flowName,
        enabled: flowEnabled,
        nodes: nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
        edges: edges.map(e => ({
          id: e.id, source: e.source, target: e.target,
          sourceHandle: e.sourceHandle ?? null,
          targetHandle: e.targetHandle ?? null,
        })),
      };
      if (activeFlowId) {
        const updated = await api.put<ButlerFlow>(`/butler/flows/${activeFlowId}`, payload);
        setFlows(fs => fs.map(f => f.id === activeFlowId ? updated : f));
      } else {
        const created = await api.post<ButlerFlow>("/butler/flows", payload);
        setFlows(fs => [...fs, created]);
        setActiveId(created.id);
      }
      showToast(t("common.saved"));
    } catch (e) {
      showToast(e instanceof Error ? e.message : t("common.saveError"));
    } finally {
      setSaving(false);
    }
  };

  const deleteFlow = async () => {
    if (!activeFlowId || !confirm(t("common.confirmDelete", { name: flowName }))) return;
    try {
      await api.delete(`/butler/flows/${activeFlowId}`);
      setFlows(fs => fs.filter(f => f.id !== activeFlowId));
      newFlow();
      showToast(t("common.deleted"));
    } catch (e) {
      showToast(e instanceof Error ? e.message : t("common.error"));
    }
  };

  const toggleFlow = async () => {
    if (!activeFlowId) { setEnabled(e => !e); return; }
    try {
      const res = await api.patch<{ enabled: boolean }>(`/butler/flows/${activeFlowId}/toggle`, {});
      setEnabled(res.enabled);
      setFlows(fs => fs.map(f => f.id === activeFlowId ? { ...f, enabled: res.enabled } : f));
    } catch (e) {
      showToast(e instanceof Error ? e.message : t("common.error"));
    }
  };

  const onConnect = useCallback((c: Connection) => {
    setEdges(es => addEdge({
      ...c,
      animated: true,
      style: { stroke: "#6366f1", strokeWidth: 2 },
    }, es));
  }, [setEdges]);

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData("application/butler-node");
    if (!raw) return;
    const { type, subtype, label } = JSON.parse(raw) as { type: string; subtype: string; label: string };
    const position = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setNodes(ns => [...ns, {
      id: genId(type),
      type,
      position,
      data: { subtype, label, params: defaultParams(subtype) },
    } as BNode]);
  }, [rf, setNodes]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedId(node.id);
  }, []);

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const selectedNode = nodes.find(n => n.id === selectedId) as BNode | undefined;

  const updateParams = (params: Record<string, unknown>) => {
    if (!selectedId) return;
    setNodes(ns => ns.map(n =>
      n.id === selectedId ? { ...n, data: { ...n.data, params } } : n
    ) as BNode[]);
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    setNodes(ns => (ns as BNode[]).filter(n => n.id !== selectedId));
    setEdges(es => es.filter(e => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  };

  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");

  return (
    <div className="flex h-full flex-col">
      {/* ── Top bar ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-2.5 shrink-0">
        <Workflow className="h-5 w-5 text-indigo-400 shrink-0" />
        <h1 className="text-base font-semibold text-white mr-1">Butler</h1>

        {/* Flow selector */}
        <select
          value={activeFlowId || ""}
          onChange={e => {
            const flow = flows.find(f => f.id === e.target.value);
            if (flow) loadFlow(flow); else newFlow();
          }}
          className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/50"
        >
          <option value="">— Neuer Flow —</option>
          {flows.map(f => (
            <option key={f.id} value={f.id}>{f.name}{f.enabled ? "" : " (inaktiv)"}</option>
          ))}
        </select>

        {/* Name */}
        <input
          type="text"
          value={flowName}
          onChange={e => setFlowName(e.target.value)}
          placeholder="Flow-Name"
          className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white placeholder-white/25 focus:outline-none focus:border-indigo-500/50 w-40"
        />

        {/* Toggle */}
        <button type="button" onClick={toggleFlow}
          className={cn(
            "flex items-center gap-1.5 text-sm px-2.5 py-1.5 rounded-lg border transition-colors",
            flowEnabled
              ? "border-green-500/40 bg-green-950/30 text-green-400 hover:bg-green-950/50"
              : "border-white/15 bg-zinc-900 text-white/35 hover:bg-white/10"
          )}
        >
          {flowEnabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
          {flowEnabled ? "Aktiv" : "Inaktiv"}
        </button>

        <div className="flex-1" />

        <button type="button" onClick={newFlow}
          className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-zinc-900 px-2.5 py-1.5 text-sm text-white hover:bg-white/10 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("common.new")}
        </button>

        <button type="button" onClick={saveFlow} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? t("common.saving") : t("common.save")}
        </button>

        {activeFlowId && (
          <button type="button" onClick={deleteFlow}
            className="flex items-center gap-1.5 rounded-lg border border-red-500/40 bg-red-950/20 px-2.5 py-1.5 text-sm text-red-400 hover:bg-red-950/40 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* ── Toast ── */}
      {toast && (
        <div className="px-4 py-1.5 bg-indigo-900/40 border-b border-indigo-500/30 text-sm text-indigo-200">
          {toast}
        </div>
      )}

      {/* ── Main area ── */}
      <div className="flex flex-1 overflow-hidden">
        <NodePalette />

        {/* Canvas */}
        <div className="flex-1 relative" onDrop={onDrop} onDragOver={onDragOver}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={NODE_TYPES}
            colorMode={isDark ? "dark" : "light"}
            fitView
            snapToGrid
            snapGrid={[15, 15]}
            defaultEdgeOptions={{
              animated: true,
              style: { stroke: "#6366f1", strokeWidth: 2 },
            }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1}
              color="rgba(255,255,255,0.05)"
            />
            <Controls />
            <MiniMap
              nodeColor={n =>
                n.type === "triggerNode"   ? "#22c55e" :
                n.type === "conditionNode" ? "#3b82f6" : "#f97316"
              }
            />
            {nodes.length === 0 && (
              <Panel position="top-center" style={{ marginTop: 48 }}>
                <div className="text-center pointer-events-none">
                  <p className="text-white/25 text-base">Knoten aus der Palette auf die Canvas ziehen</p>
                  <p className="text-white/15 text-sm mt-1">Verbinden → Speichern → Läuft automatisch</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>

        {/* Properties panel */}
        {selectedNode && (
          <PropertiesPanel
            node={selectedNode}
            agents={agents}
            onChange={updateParams}
            onDelete={deleteSelected}
          />
        )}
      </div>
    </div>
  );
}
