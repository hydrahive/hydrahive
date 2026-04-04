import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  Handle, Position, BackgroundVariant, Panel,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import {
  GitBranch, KeyRound, BookOpen, Brain, Shield, Bot, Save, Loader2, X,
  Wrench, Server, Puzzle, Cpu, PlusCircle, Rocket, Sparkles, ChevronDown,
  Play, GitFork, Square, Database, Workflow,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

// ── Node-Typen ────────────────────────────────────────────────────────────────

function RepoNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[240px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-blue-950/60 border-blue-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <GitBranch className="h-3 w-3 text-blue-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-blue-400">Repository</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Repo"}</p>
      {data.config?.url && <p className="text-[0.6rem] text-blue-400/60 mt-0.5 font-mono truncate">{data.config.url}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#60a5fa", border: "2px solid #1d4ed8", width: 10, height: 10 }} />
    </div>
  );
}

function CredentialNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-orange-950/60 border-orange-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <KeyRound className="h-3 w-3 text-orange-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-orange-400">Credential</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Token"}</p>
      {data.config?.key && <p className="text-[0.6rem] text-orange-400/60 mt-0.5 font-mono">{data.config.key}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#fb923c", border: "2px solid #9a3412", width: 10, height: 10 }} />
    </div>
  );
}

function SkillNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-purple-950/60 border-purple-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <BookOpen className="h-3 w-3 text-purple-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-purple-400">Skill</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Skill"}</p>
      {data.config?.file && <p className="text-[0.6rem] text-purple-400/60 mt-0.5 font-mono">{data.config.file}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#c084fc", border: "2px solid #6b21a8", width: 10, height: 10 }} />
    </div>
  );
}

function MemoryNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-teal-950/60 border-teal-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Brain className="h-3 w-3 text-teal-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-teal-400">Memory</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Memory"}</p>
      {data.config?.file && <p className="text-[0.6rem] text-teal-400/60 mt-0.5 font-mono">{data.config.file}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#2dd4bf", border: "2px solid #0f766e", width: 10, height: 10 }} />
    </div>
  );
}

function ToolPolicyNode({ data, selected }: { data: any; selected: boolean }) {
  const allowed = data.config?.allowed !== false;
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none", selected && "ring-2 ring-white/25",
      allowed ? "bg-green-950/60 border-green-500/60" : "bg-red-950/60 border-red-500/60")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Shield className={cn("h-3 w-3", allowed ? "text-green-400" : "text-red-400")} />
        <span className={cn("text-[0.55rem] font-bold uppercase tracking-widest", allowed ? "text-green-400" : "text-red-400")}>
          Tool {allowed ? "✓" : "✗"}
        </span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Tool Policy"}</p>
      {data.config?.tool && <p className={cn("text-[0.6rem] mt-0.5 font-mono", allowed ? "text-green-400/60" : "text-red-400/60")}>{data.config.tool}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: allowed ? "#4ade80" : "#f87171", border: "2px solid #166534", width: 10, height: 10 }} />
    </div>
  );
}

function ToolNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[140px] max-w-[200px] rounded-xl border-2 px-3 py-2 shadow-lg select-none bg-cyan-950/60 border-cyan-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-0.5">
        <Wrench className="h-3 w-3 text-cyan-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-cyan-400">Tool</span>
      </div>
      <p className="text-xs font-medium text-white leading-tight font-mono">{data.label || "tool"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#22d3ee", border: "2px solid #0e7490", width: 10, height: 10 }} />
    </div>
  );
}

function McpNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-pink-950/60 border-pink-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Server className="h-3 w-3 text-pink-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-pink-400">MCP Server</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "MCP"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#f472b6", border: "2px solid #9d174d", width: 10, height: 10 }} />
    </div>
  );
}

function PluginNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[160px] max-w-[220px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-amber-950/60 border-amber-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Puzzle className="h-3 w-3 text-amber-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-amber-400">Plugin</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Plugin"}</p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#fbbf24", border: "2px solid #92400e", width: 10, height: 10 }} />
    </div>
  );
}

function AgentProfileNode({ data, selected }: { data: any; selected: boolean }) {
  const isNew = data.config?.isNew;
  const hBase = { width: 10, height: 10, border: "2px solid #52525b" };
  return (
    <div className={cn("min-w-[240px] rounded-xl border-2 px-4 py-3 shadow-lg select-none relative",
      isNew ? "bg-indigo-950/70 border-indigo-400/60" : "bg-zinc-800/80 border-zinc-400/40",
      selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Bot className={cn("h-3.5 w-3.5", isNew ? "text-indigo-300" : "text-zinc-300")} />
        <span className={cn("text-[0.55rem] font-bold uppercase tracking-widest", isNew ? "text-indigo-400" : "text-zinc-400")}>
          {isNew ? "Neuer Agent" : "Agent"}
        </span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Agent"}</p>
      {data.config?.model && <p className="text-[0.6rem] text-indigo-400/50 mt-0.5 font-mono">{data.config.model}</p>}
      {data.config?.type && <p className="text-[0.55rem] text-white/30 mt-0.5">{data.config.type}</p>}

      {/* Handle-Labels */}
      <span className="absolute -left-1 top-[14%] -translate-x-full text-[0.5rem] text-cyan-400/60">Tools</span>
      <span className="absolute -right-1 top-[14%] translate-x-full text-[0.5rem] text-pink-400/60">MCP</span>
      <span className="absolute -left-1 top-[45%] -translate-x-full text-[0.5rem] text-purple-400/60">Skills</span>
      <span className="absolute -right-1 top-[45%] translate-x-full text-[0.5rem] text-amber-400/60">Plugins</span>
      <span className="absolute -left-1 top-[76%] -translate-x-full text-[0.5rem] text-violet-400/60">Workflow</span>
      <span className="absolute left-1/2 -translate-x-1/2 -top-1 -translate-y-full text-[0.5rem] text-teal-400/60">Memory</span>
      <span className="absolute left-1/2 -translate-x-1/2 -bottom-1 translate-y-full text-[0.5rem] text-blue-400/60">Repos</span>

      {/* Handles: Links oben=Tools, links mitte=Skills, links unten=Workflow */}
      <Handle type="target" position={Position.Left} id="tools" style={{ ...hBase, background: "#22d3ee", top: "16%" }} />
      <Handle type="target" position={Position.Left} id="skills" style={{ ...hBase, background: "#c084fc", top: "48%" }} />
      <Handle type="target" position={Position.Left} id="workflow" style={{ ...hBase, background: "#8b5cf6", top: "80%" }} />
      {/* Handles: Rechts oben=MCP, rechts unten=Plugins */}
      <Handle type="target" position={Position.Right} id="mcp" style={{ ...hBase, background: "#f472b6", top: "16%" }} />
      <Handle type="target" position={Position.Right} id="plugins" style={{ ...hBase, background: "#fbbf24", top: "48%" }} />
      {/* Handles: Oben=Memory, Unten=Repos */}
      <Handle type="target" position={Position.Top} id="memory" style={{ ...hBase, background: "#2dd4bf" }} />
      <Handle type="target" position={Position.Bottom} id="repos" style={{ ...hBase, background: "#60a5fa" }} />
    </div>
  );
}

// ── Workflow-Flow Nodes ───────────────────────────────────────────────────────

function StepFlowNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[260px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-indigo-950/60 border-indigo-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Play className="h-3 w-3 text-indigo-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-indigo-400">Schritt</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Schritt"}</p>
      {data.toolId && <p className="text-[0.6rem] text-indigo-400/60 mt-0.5 font-mono">{data.toolId}</p>}
      {data.description && <p className="text-[0.55rem] text-white/30 mt-0.5 leading-snug">{data.description}</p>}
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#818cf8", border: "2px solid #4338ca", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#818cf8", border: "2px solid #4338ca", width: 10, height: 10 }} />
    </div>
  );
}

function SourceFlowNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[240px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-emerald-950/60 border-emerald-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Database className="h-3 w-3 text-emerald-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-emerald-400">Quelle</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Quelle"}</p>
      {data.sourceType && <p className="text-[0.6rem] text-emerald-400/60 mt-0.5">{data.sourceType}: {data.sourceId || "—"}</p>}
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#34d399", border: "2px solid #065f46", width: 10, height: 10 }} />
    </div>
  );
}

function BranchFlowNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[180px] max-w-[260px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-amber-950/60 border-amber-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <GitFork className="h-3 w-3 text-amber-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-amber-400">Entscheidung</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Bedingung?"}</p>
      {data.condition && <p className="text-[0.55rem] text-white/30 mt-0.5 leading-snug">{data.condition}</p>}
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#fbbf24", border: "2px solid #92400e", width: 10, height: 10 }} />
      <Handle type="source" position={Position.Right} id="true" style={{ background: "#4ade80", border: "2px solid #166534", width: 10, height: 10, top: "30%" }} />
      <Handle type="source" position={Position.Right} id="false" style={{ background: "#f87171", border: "2px solid #991b1b", width: 10, height: 10, top: "70%" }} />
      <span className="absolute -right-1 translate-x-full text-[0.5rem] text-green-400/60" style={{ top: "26%" }}>Ja</span>
      <span className="absolute -right-1 translate-x-full text-[0.5rem] text-red-400/60" style={{ top: "66%" }}>Nein</span>
    </div>
  );
}

function EndFlowNode({ data, selected }: { data: any; selected: boolean }) {
  return (
    <div className={cn("min-w-[140px] max-w-[200px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-zinc-800/60 border-zinc-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Square className="h-3 w-3 text-zinc-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-zinc-400">Ende</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Ende"}</p>
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#a1a1aa", border: "2px solid #52525b", width: 10, height: 10 }} />
    </div>
  );
}

function WorkflowOverviewNode({ data, selected }: { data: any; selected: boolean }) {
  const stepCount = data.stepCount ?? 0;
  const hasFlow = stepCount > 0;
  return (
    <div className={cn("min-w-[180px] max-w-[240px] rounded-xl border-2 px-3 py-2.5 shadow-lg select-none bg-violet-950/60 border-violet-500/60", selected && "ring-2 ring-white/25")}>
      <div className="flex items-center gap-1.5 mb-1">
        <Workflow className="h-3 w-3 text-violet-400" />
        <span className="text-[0.55rem] font-bold uppercase tracking-widest text-violet-400">Workflow</span>
      </div>
      <p className="text-sm font-medium text-white leading-tight">{data.label || "Arbeitsablauf"}</p>
      <p className="text-[0.6rem] text-violet-400/60 mt-0.5">
        {hasFlow ? `${stepCount} Nodes definiert` : "Noch kein Workflow"}
      </p>
      <Handle type="source" position={Position.Right} id="out" style={{ background: "#8b5cf6", border: "2px solid #5b21b6", width: 10, height: 10 }} />
    </div>
  );
}

const NODE_TYPES = {
  repository:       RepoNode             as any,
  credential:       CredentialNode       as any,
  skill:            SkillNode            as any,
  memory:           MemoryNode           as any,
  toolpolicy:       ToolPolicyNode       as any,
  tool:             ToolNode             as any,
  mcp:              McpNode              as any,
  plugin:           PluginNode           as any,
  agentprofile:     AgentProfileNode     as any,
  workflowOverview: WorkflowOverviewNode as any,
  stepFlow:         StepFlowNode         as any,
  sourceFlow:       SourceFlowNode       as any,
  branchFlow:       BranchFlowNode       as any,
  endFlow:          EndFlowNode          as any,
};

// ── Palette ───────────────────────────────────────────────────────────────────

const PALETTE_ITEMS = [
  { type: "tool",             label: "Tool",         icon: Wrench,    color: "text-cyan-400" },
  { type: "skill",            label: "Skill",        icon: BookOpen,  color: "text-purple-400" },
  { type: "memory",           label: "Memory",       icon: Brain,     color: "text-teal-400" },
  { type: "mcp",              label: "MCP Server",   icon: Server,    color: "text-pink-400" },
  { type: "plugin",           label: "Plugin",       icon: Puzzle,    color: "text-amber-400" },
  { type: "repository",       label: "Repository",   icon: GitBranch, color: "text-blue-400" },
  { type: "credential",       label: "Credential",   icon: KeyRound,  color: "text-orange-400" },
  { type: "toolpolicy",       label: "Tool-Policy",  icon: Shield,    color: "text-green-400" },
  { type: "workflowOverview", label: "Workflow",     icon: Workflow,  color: "text-violet-400" },
];

const MODELS = [
  "claude-sonnet-4-6",
  "claude-opus-4-6",
  "claude-haiku-4-5-20251001",
  "gpt-4.1",
  "gpt-4.1-mini",
  "ollama/llama3.3",
  "ollama/qwen3",
  "ollama/gemma3",
];

// ── Properties Panel ──────────────────────────────────────────────────────────

function PropertiesPanel({ node, onChange, onDelete, availableTools, availableMcp, availablePlugins, availableSkills, viewMode, onEditWorkflow }: {
  node: Node | null;
  onChange: (id: string, data: any) => void;
  onDelete: (id: string) => void;
  availableTools: string[];
  availableMcp: string[];
  availablePlugins: string[];
  availableSkills: string[];
  viewMode?: ViewMode;
  onEditWorkflow?: () => void;
}) {
  if (!node) return (
    <div className="p-4 text-xs text-white/20 space-y-4 overflow-y-auto h-full">
      {viewMode === "workflow" ? (
        <div className="space-y-3 text-left">
          <p className="text-indigo-300/80 font-bold text-sm">Workflow-Editor — Anleitung</p>

          <div className="space-y-1.5">
            <p className="text-white/40 font-semibold">Was ist ein Agent-Workflow?</p>
            <p className="text-white/25 leading-relaxed">
              Ein Workflow definiert den Arbeitsablauf, den ein Agent bei jeder Aufgabe befolgt.
              Statt dem Agent nur Tools und eine Persönlichkeit zu geben, legst du hier fest
              <strong className="text-white/40"> in welcher Reihenfolge</strong> er arbeiten soll.
            </p>
          </div>

          <div className="space-y-1.5">
            <p className="text-white/40 font-semibold">So erstellst du einen Workflow:</p>
            <ol className="list-decimal list-inside space-y-1 text-white/25 leading-relaxed">
              <li>Wähle oben einen Agenten aus dem Dropdown</li>
              <li>Klicke auf <strong className="text-white/40">Palette</strong> in der Toolbar</li>
              <li>Füge Nodes hinzu: <strong className="text-indigo-300/60">Schritt</strong>, <strong className="text-emerald-300/60">Quelle</strong>, <strong className="text-amber-300/60">Entscheidung</strong>, <strong className="text-zinc-300/60">Ende</strong></li>
              <li>Verbinde die Nodes: Ziehe vom rechten Handle (Ausgang) zum linken Handle (Eingang) des nächsten Nodes</li>
              <li>Klicke auf einen Node um rechts seine Eigenschaften zu bearbeiten</li>
              <li>Klicke <strong className="text-white/40">Speichern</strong></li>
            </ol>
          </div>

          <div className="space-y-1.5">
            <p className="text-white/40 font-semibold">Node-Typen:</p>
            <div className="space-y-1.5">
              <p><strong className="text-indigo-300/60">Schritt</strong> — Ein Arbeitsschritt. Optional mit Tool verknüpfen (z.B. file_read, git_diff).</p>
              <p><strong className="text-emerald-300/60">Quelle</strong> — Startpunkt: eine Datenquelle (Repo, Memory, Skill, URL) die der Agent zuerst lesen soll.</p>
              <p><strong className="text-amber-300/60">Entscheidung</strong> — Verzweigung mit Ja/Nein. Der grüne Ausgang = Ja, der rote = Nein.</p>
              <p><strong className="text-zinc-300/60">Ende</strong> — Workflow abgeschlossen. Der Agent gibt seine Antwort aus.</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <p className="text-white/40 font-semibold">Beispiel: Code-Reviewer</p>
            <p className="text-white/25 leading-relaxed">
              Quelle (Repo) → Schritt (git_diff) → Entscheidung ("Gibt es Probleme?")
              → Ja: Schritt (Issue erstellen) → Ende
              → Nein: Ende
            </p>
          </div>

          <div className="space-y-1.5">
            <p className="text-white/40 font-semibold">Tipps:</p>
            <ul className="list-disc list-inside space-y-0.5 text-white/25 leading-relaxed">
              <li>Flow von links nach rechts aufbauen</li>
              <li>Jeder Flow braucht mindestens einen Ende-Node</li>
              <li>Der Agent sieht den Workflow als nummerierte Arbeitsanweisung</li>
              <li>Ressourcen (Tools, MCP etc.) im Tab "Ressourcen" zuweisen</li>
              <li>Workflow und Ressourcen werden getrennt gespeichert</li>
            </ul>
          </div>
        </div>
      ) : (
        <p className="text-center">Node auswählen um Eigenschaften zu bearbeiten</p>
      )}
    </div>
  );

  const n   = node;
  const d   = n.data as any;
  const cfg = d.config || {};
  const upd    = (patch: any) => onChange(n.id, { ...d, ...patch });
  const updCfg = (patch: any) => upd({ config: { ...cfg, ...patch } });

  return (
    <div className="p-3 space-y-3 overflow-y-auto">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-white/40 uppercase tracking-wider">{n.type}</span>
        <button onClick={() => onDelete(n.id)} className="p-1 rounded text-red-400 hover:bg-red-500/15 transition-colors"><X className="h-3.5 w-3.5" /></button>
      </div>

      <div>
        <label className="block text-[0.65rem] text-white/40 mb-1">Bezeichnung</label>
        <input value={d.label || ""} onChange={e => upd({ label: e.target.value })}
          className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
      </div>

      {/* Agent-Profil Properties */}
      {n.type === "agentprofile" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Agent-ID</label>
          <input value={cfg.agentId || ""} onChange={e => updCfg({ agentId: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })}
            placeholder="mein-agent"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-indigo-500/60" />
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Typ</label>
          <select value={cfg.type || "specialist"} onChange={e => updCfg({ type: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-sm text-white focus:outline-none">
            <option value="boss">Boss</option>
            <option value="worker">Worker</option>
            <option value="specialist">Specialist</option>
          </select>
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">LLM Model</label>
          <select value={cfg.model || "claude-sonnet-4-6"} onChange={e => updCfg({ model: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            {MODELS.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Soul / Persönlichkeit</label>
          <textarea value={cfg.soul || ""} onChange={e => updCfg({ soul: e.target.value })}
            rows={4} placeholder="Du bist ein hilfreicher Assistent..."
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500/60 resize-none" />
        </div>
      </>}

      {/* Tool Properties */}
      {n.type === "tool" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Tool-ID</label>
          <select value={cfg.toolId || ""} onChange={e => { updCfg({ toolId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availableTools.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </>}

      {/* MCP Properties */}
      {n.type === "mcp" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">MCP Server</label>
          <select value={cfg.serverId || ""} onChange={e => { updCfg({ serverId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availableMcp.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </>}

      {/* Plugin Properties */}
      {n.type === "plugin" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Plugin</label>
          <select value={cfg.pluginId || ""} onChange={e => { updCfg({ pluginId: e.target.value }); upd({ label: e.target.value }); }}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— wählen —</option>
            {availablePlugins.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </>}

      {n.type === "repository" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">URL</label>
          <input value={cfg.url || ""} onChange={e => updCfg({ url: e.target.value })}
            placeholder="https://gitea.intern/owner/repo"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-blue-500/60" />
        </div>
      </>}

      {n.type === "credential" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Config-Key</label>
          <input value={cfg.key || ""} onChange={e => updCfg({ key: e.target.value })}
            placeholder="gitea_token"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500/60" />
        </div>
      </>}

      {n.type === "skill" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Skill</label>
          {availableSkills.length > 0 ? (
            <select value={cfg.file || ""} onChange={e => { updCfg({ file: e.target.value }); upd({ label: e.target.value }); }}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
              <option value="">— wählen —</option>
              {availableSkills.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          ) : (
            <input value={cfg.file || ""} onChange={e => updCfg({ file: e.target.value })}
              placeholder="skill_name.md"
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-purple-500/60" />
          )}
        </div>
      </>}

      {n.type === "memory" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Datei / Ordner</label>
          <input value={cfg.file || ""} onChange={e => updCfg({ file: e.target.value })}
            placeholder="project_notes.md"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-teal-500/60" />
        </div>
      </>}

      {n.type === "toolpolicy" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Tool-ID</label>
          <input value={cfg.tool || ""} onChange={e => updCfg({ tool: e.target.value })}
            placeholder="shell_exec"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none" />
        </div>
        <div className="flex items-center gap-3">
          <label className={cn("flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border text-xs transition-colors",
            cfg.allowed !== false ? "bg-green-950/60 border-green-500/40 text-green-300" : "bg-zinc-800 border-white/10 text-white/30")}
            onClick={() => updCfg({ allowed: true })}>
            <Shield className="h-3 w-3" /> Erlaubt
          </label>
          <label className={cn("flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border text-xs transition-colors",
            cfg.allowed === false ? "bg-red-950/60 border-red-500/40 text-red-300" : "bg-zinc-800 border-white/10 text-white/30")}
            onClick={() => updCfg({ allowed: false })}>
            <X className="h-3 w-3" /> Gesperrt
          </label>
        </div>
      </>}

      {/* Workflow Overview (Ressourcen-Ansicht) */}
      {n.type === "workflowOverview" && <>
        <p className="text-[0.65rem] text-white/30 leading-relaxed">
          Dieser Node verbindet den Arbeitsablauf mit dem Agenten. Klicke unten um den Workflow zu bearbeiten.
        </p>
        {onEditWorkflow && (
          <button onClick={onEditWorkflow}
            className="flex items-center gap-1.5 w-full justify-center rounded-lg bg-violet-600 hover:bg-violet-700 px-3 py-2 text-xs text-white transition-colors">
            <Workflow className="h-3.5 w-3.5" /> Workflow bearbeiten
          </button>
        )}
      </>}

      {/* ── Workflow-Flow Node Properties ── */}
      {n.type === "stepFlow" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Beschreibung</label>
          <textarea value={d.description || ""} onChange={e => upd({ description: e.target.value })}
            rows={3} placeholder="Was soll in diesem Schritt passieren?"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500/60 resize-none" />
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Tool (optional)</label>
          <select value={d.toolId || ""} onChange={e => upd({ toolId: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none">
            <option value="">— kein Tool —</option>
            {availableTools.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </>}

      {n.type === "sourceFlow" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Quellen-Typ</label>
          <select value={d.sourceType || ""} onChange={e => upd({ sourceType: e.target.value })}
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none">
            <option value="">— wählen —</option>
            <option value="repo">Repository</option>
            <option value="memory">Memory</option>
            <option value="skill">Skill</option>
            <option value="url">URL</option>
          </select>
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Ressource-ID</label>
          <input value={d.sourceId || ""} onChange={e => upd({ sourceId: e.target.value })}
            placeholder="z.B. best-practices.md"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white font-mono focus:outline-none focus:border-emerald-500/60" />
        </div>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Beschreibung</label>
          <textarea value={d.description || ""} onChange={e => upd({ description: e.target.value })}
            rows={2} placeholder="Was liefert diese Quelle?"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500/60 resize-none" />
        </div>
      </>}

      {n.type === "branchFlow" && <>
        <div>
          <label className="block text-[0.65rem] text-white/40 mb-1">Bedingung</label>
          <textarea value={d.condition || ""} onChange={e => upd({ condition: e.target.value })}
            rows={3} placeholder="Unter welcher Bedingung Ja/Nein?"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500/60 resize-none" />
        </div>
      </>}

      {n.type === "endFlow" && (
        <p className="text-[0.65rem] text-white/30">End-Node — der Agent gibt hier seine Antwort aus.</p>
      )}
    </div>
  );
}

// ── Flow Palette ─────────────────────────────────────────────────────────────

const FLOW_PALETTE_ITEMS = [
  { type: "stepFlow",   label: "Schritt",      icon: Play,    color: "text-indigo-400" },
  { type: "sourceFlow", label: "Quelle",       icon: Database, color: "text-emerald-400" },
  { type: "branchFlow", label: "Entscheidung", icon: GitFork, color: "text-amber-400" },
  { type: "endFlow",    label: "Ende",         icon: Square,  color: "text-zinc-400" },
];

// ── Inner Component ───────────────────────────────────────────────────────────

interface AgentEntry { id: string; identity: string }

type ViewMode = "resources" | "workflow";

function AgentBlueprintInner({ agents }: { agents: AgentEntry[] }) {
  const { t } = useTranslation();
  const [selectedAgentId, setSelectedAgentId] = useState<string>(agents[0]?.id ?? "");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode]  = useState<Node | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast,   setToast]   = useState<string | null>(null);
  const [isNewMode, setIsNewMode] = useState(false);
  const [creating, setCreating]   = useState(false);
  const [availableTools, setAvailableTools]     = useState<string[]>([]);
  const [availableMcp, setAvailableMcp]         = useState<string[]>([]);
  const [availablePlugins, setAvailablePlugins] = useState<string[]>([]);
  const [availableSkills, setAvailableSkills]   = useState<string[]>([]);
  const [showPalette, setShowPalette]           = useState(false);
  const [viewMode, setViewMode]                 = useState<ViewMode>("resources");
  // Workflow-Flow eigener State (unabhängig von Ressourcen-Blueprint)
  const [flowNodes, setFlowNodes, onFlowNodesChange] = useNodesState<Node>([]);
  const [flowEdges, setFlowEdges, onFlowEdgesChange] = useEdgesState<Edge>([]);
  const [flowLoading, setFlowLoading] = useState(false);
  const rf = useReactFlow();

  // Verfügbare Tools, MCP-Server, Plugins laden
  useEffect(() => {
    // /tools gibt Dict {tool_id: {name, description, ...}} zurück
    api.get<Record<string, any>>("/tools").then(tools => {
      setAvailableTools(Object.keys(tools).sort());
    }).catch(() => {});
    api.get<{servers:{id:string}[]}>("/mcp/servers").then(d => {
      setAvailableMcp((d.servers || []).map(s => s.id));
    }).catch(() => {});
    api.pluginsList().then(d => {
      setAvailablePlugins(d.plugins.filter(p => p.enabled).map(p => p.id));
    }).catch(() => {});
  }, []);

  // Blueprint laden — oder aus Agent-Config generieren
  useEffect(() => {
    if (!selectedAgentId || isNewMode) return;
    setLoading(true);
    setSelectedNode(null);

    (async () => {
      try {
        // Erst gespeichertes Blueprint laden
        const wf = await api.get<{ nodes: any[]; edges: any[] }>(`/agents/${selectedAgentId}/workflow-blueprint`);
        if (wf.nodes && wf.nodes.length > 0) {
          setNodes(wf.nodes);
          setEdges(wf.edges || []);
          setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
          return;
        }
      } catch { /* kein Blueprint gespeichert */ }

      // Kein Blueprint → aus Agent-Config generieren
      try {
        const agentData = await api.get<Record<string, any>>("/agents");
        const agent = agentData[selectedAgentId];
        if (!agent?.config) { setNodes([]); setEdges([]); return; }
        const cfg = agent.config;
        const identity = cfg.identity || selectedAgentId;
        const genNodes: Node[] = [];
        const genEdges: Edge[] = [];
        const agentNodeId = "agent-center";
        const edgeStyle = { stroke: "#6366f1", strokeWidth: 2 };

        // Agent-Node in der Mitte
        genNodes.push({
          id: agentNodeId, type: "agentprofile",
          position: { x: 400, y: 250 },
          data: { label: identity, config: { agentId: selectedAgentId, type: cfg.type, model: cfg.llm?.model } },
        });

        // Tools links vom Agent
        const tools: string[] = cfg.tools || [];
        tools.forEach((t: string, i: number) => {
          const nodeId = `tool-${t}`;
          genNodes.push({
            id: nodeId, type: "tool",
            position: { x: 60, y: 80 + i * 50 },
            data: { label: t, config: { toolId: t } },
          });
          genEdges.push({ id: `e-${nodeId}`, source: nodeId, sourceHandle: "out", target: agentNodeId, targetHandle: "tools", animated: true, style: { ...edgeStyle, stroke: "#22d3ee" } });
        });

        // MCP-Server rechts oben
        const mcps: string[] = cfg.mcp_servers || [];
        mcps.forEach((s: string, i: number) => {
          const nodeId = `mcp-${s}`;
          genNodes.push({
            id: nodeId, type: "mcp",
            position: { x: 750, y: 80 + i * 60 },
            data: { label: s, config: { serverId: s } },
          });
          genEdges.push({ id: `e-${nodeId}`, source: nodeId, sourceHandle: "out", target: agentNodeId, targetHandle: "mcp", animated: true, style: { ...edgeStyle, stroke: "#f472b6" } });
        });

        // Skills links unten
        try {
          const skillsResp = await api.get<{skills:{filename:string;skill?:string}[]}>(`/agents/${selectedAgentId}/skills`);
          const skills = skillsResp.skills || [];
          const toolCount = tools.length;
          skills.forEach((s, i) => {
            const nodeId = `skill-${s.filename}`;
            genNodes.push({
              id: nodeId, type: "skill",
              position: { x: 60, y: 80 + (toolCount + i) * 50 + 30 },
              data: { label: s.skill || s.filename, config: { file: s.filename } },
            });
            genEdges.push({ id: `e-${nodeId}`, source: nodeId, sourceHandle: "out", target: agentNodeId, targetHandle: "skills", animated: true, style: { ...edgeStyle, stroke: "#c084fc" } });
          });
          setAvailableSkills(skills.map(s => s.filename));
        } catch { /* keine Skills */ }

        // Plugins rechts unten
        try {
          const plgResp = await api.pluginAgentGet(selectedAgentId);
          const pluginIds = plgResp.plugins || [];
          const mcpCount = mcps.length;
          pluginIds.forEach((p: string, i: number) => {
            const nodeId = `plugin-${p}`;
            genNodes.push({
              id: nodeId, type: "plugin",
              position: { x: 750, y: 80 + (mcpCount + i) * 60 + 30 },
              data: { label: p, config: { pluginId: p } },
            });
            genEdges.push({ id: `e-${nodeId}`, source: nodeId, sourceHandle: "out", target: agentNodeId, targetHandle: "plugins", animated: true, style: { ...edgeStyle, stroke: "#fbbf24" } });
          });
        } catch { /* keine Plugins */ }

        setNodes(genNodes);
        setEdges(genEdges);
        setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
      } catch {
        setNodes([]); setEdges([]);
      }
      // Workflow-Flow Node-Count in WorkflowOverview-Nodes patchen
      try {
        const flow = await api.get<{ nodes: any[] }>(`/agents/${selectedAgentId}/workflow-flow`);
        const flowCount = (flow.nodes || []).length;
        setNodes(ns => ns.map(n => n.type === "workflowOverview"
          ? { ...n, data: { ...n.data, stepCount: flowCount } }
          : n
        ));
      } catch { /* kein Workflow */ }
    })().finally(() => setLoading(false));
  }, [selectedAgentId, setNodes, setEdges, rf, isNewMode]);

  // Workflow-Flow laden wenn Agent wechselt oder Workflow-Mode aktiviert wird
  useEffect(() => {
    if (!selectedAgentId || viewMode !== "workflow" || isNewMode) return;
    setFlowLoading(true);
    setSelectedNode(null);
    api.get<{ nodes: any[]; edges: any[] }>(`/agents/${selectedAgentId}/workflow-flow`)
      .then(wf => {
        setFlowNodes(wf.nodes || []);
        setFlowEdges(wf.edges || []);
        setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
      })
      .catch(() => { setFlowNodes([]); setFlowEdges([]); })
      .finally(() => setFlowLoading(false));
  }, [selectedAgentId, viewMode, setFlowNodes, setFlowEdges, rf, isNewMode]);

  const onConnect = useCallback((c: Connection) => {
    const setter = viewMode === "workflow" ? setFlowEdges : setEdges;
    setter(es => addEdge({
      ...c, animated: true,
      style: { stroke: viewMode === "workflow" ? "#818cf8" : "#6366f1", strokeWidth: 2 },
    } as Edge, es));
  }, [setEdges, setFlowEdges, viewMode]);

  function addNode(type: string, label?: string) {
    const defaults: Record<string, string> = {
      repository: "Repo", credential: "Token", skill: "Skill",
      memory: "Memory", toolpolicy: "Tool Policy", tool: "tool",
      mcp: "MCP Server", plugin: "Plugin", workflowOverview: "Arbeitsablauf",
      agentprofile: "Neuer Agent",
      stepFlow: "Schritt", sourceFlow: "Quelle",
      branchFlow: "Bedingung?", endFlow: "Ende",
    };
    const id = `${type}-${Date.now()}`;
    const isFlow = type.endsWith("Flow");
    const targetNodes = isFlow ? flowNodes : nodes;
    const cnt = targetNodes.length;
    const config: any = {};
    if (type === "agentprofile") {
      config.isNew = isNewMode;
      config.type = "specialist";
      config.model = "claude-sonnet-4-6";
    }
    const newNode: Node = {
      id, type, position: { x: 80 + cnt * 25, y: 80 + cnt * 18 },
      data: { label: label || defaults[type] || type, config },
    };
    if (isFlow) {
      setFlowNodes(ns => [...ns, newNode]);
    } else {
      setNodes(ns => [...ns, newNode]);
    }
  }

  function startNewAgent() {
    setIsNewMode(true);
    setSelectedAgentId("");
    setSelectedNode(null);
    setNodes([{
      id: "agent-new",
      type: "agentprofile",
      position: { x: 400, y: 200 },
      data: {
        label: "Neuer Agent",
        config: { isNew: true, type: "specialist", model: "claude-sonnet-4-6", soul: "" },
      },
    }]);
    setEdges([]);
    setTimeout(() => rf.fitView({ padding: 0.3 }), 50);
  }

  function cancelNewAgent() {
    setIsNewMode(false);
    setSelectedAgentId(agents[0]?.id ?? "");
  }

  function updateNodeData(nodeId: string, data: any) {
    if (viewMode === "workflow") {
      setFlowNodes(ns => ns.map(n => n.id === nodeId ? { ...n, data } : n));
    } else {
      setNodes(ns => ns.map(n => n.id === nodeId ? { ...n, data } : n));
    }
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data } : prev);
  }

  function deleteNode(nodeId: string) {
    if (viewMode === "workflow") {
      setFlowNodes(ns => ns.filter(n => n.id !== nodeId));
      setFlowEdges(es => es.filter(e => e.source !== nodeId && e.target !== nodeId));
    } else {
      setNodes(ns => ns.filter(n => n.id !== nodeId));
      setEdges(es => es.filter(e => e.source !== nodeId && e.target !== nodeId));
    }
    setSelectedNode(null);
  }

  async function save() {
    if (!selectedAgentId) return;
    setSaving(true);
    try {
      if (viewMode === "workflow") {
        await api.put(`/agents/${selectedAgentId}/workflow-flow`, { nodes: flowNodes, edges: flowEdges });
      } else {
        await api.put(`/agents/${selectedAgentId}/workflow-blueprint`, { nodes, edges });
      }
      setToast(t("common.saved"));
      setTimeout(() => setToast(null), 3000);
    } catch (e) {
      setToast(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  async function createAgent() {
    // Agent-Node finden
    const agentNode = nodes.find(n => n.type === "agentprofile");
    if (!agentNode) { setToast("Kein Agent-Node gefunden"); return; }
    const cfg = (agentNode.data as any).config || {};
    const agentId = cfg.agentId?.trim();
    if (!agentId) { setToast("Agent-ID fehlt"); return; }
    const identity = (agentNode.data as any).label?.trim() || agentId;

    // Verbundene Nodes sammeln
    const connectedIds = new Set(
      edges.filter(e => e.target === agentNode.id).map(e => e.source)
    );
    const connected = nodes.filter(n => connectedIds.has(n.id));

    const tools = connected
      .filter(n => n.type === "tool")
      .map(n => (n.data as any).config?.toolId)
      .filter(Boolean);

    const mcpServers = connected
      .filter(n => n.type === "mcp")
      .map(n => (n.data as any).config?.serverId)
      .filter(Boolean);

    setCreating(true);
    try {
      await api.post("/agents", {
        id: agentId,
        type: cfg.type || "specialist",
        identity,
        model: cfg.model || "claude-sonnet-4-6",
        soul: cfg.soul || "",
        tools: tools.length > 0 ? tools : ["file_read", "web_search", "read_memory", "write_memory"],
        mcp_servers: mcpServers,
      });

      // Plugins zuweisen
      const pluginIds = connected
        .filter(n => n.type === "plugin")
        .map(n => (n.data as any).config?.pluginId)
        .filter(Boolean);
      if (pluginIds.length > 0) {
        await api.pluginAgentSet(agentId, pluginIds).catch(() => {});
      }

      // Blueprint speichern
      await api.put(`/agents/${agentId}/workflow-blueprint`, { nodes, edges }).catch(() => {});

      setToast(`Agent "${identity}" erstellt!`);
      setIsNewMode(false);
      setTimeout(() => setToast(null), 4000);
      // Agent zur Liste hinzufügen und auswählen
      agents.push({ id: agentId, identity });
      setSelectedAgentId(agentId);
    } catch (e: any) {
      setToast(e.message || "Fehler beim Erstellen");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0 flex-wrap">
        {!isNewMode ? (
          <>
            <select value={selectedAgentId} onChange={e => setSelectedAgentId(e.target.value)}
              className="rounded-lg bg-zinc-900 border border-white/15 px-2.5 py-1.5 text-sm text-white focus:outline-none">
              {agents.map(a => <option key={a.id} value={a.id}>{a.identity}</option>)}
            </select>
            <button onClick={startNewAgent}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 text-xs text-white transition-colors">
              <PlusCircle className="h-3.5 w-3.5" /> Neuer Agent
            </button>
            {selectedAgentId && !selectedAgentId.startsWith("personal_") && (
              <button onClick={async () => {
                const name = agents.find(a => a.id === selectedAgentId)?.identity || selectedAgentId;
                if (!confirm(`Agent "${name}" wirklich löschen?`)) return;
                try {
                  await api.delete(`/agents/${selectedAgentId}`);
                  const idx = agents.findIndex(a => a.id === selectedAgentId);
                  if (idx >= 0) agents.splice(idx, 1);
                  setSelectedAgentId(agents[0]?.id ?? "");
                  setToast(`Agent "${name}" gelöscht`);
                  setTimeout(() => setToast(null), 3000);
                } catch (e: any) { setToast(e.message); }
              }}
                className="flex items-center gap-1.5 rounded-lg border border-red-500/40 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/15 transition-colors">
                <X className="h-3 w-3" /> Löschen
              </button>
            )}
          </>
        ) : (
          <>
            <span className="flex items-center gap-1.5 text-sm text-indigo-300 font-medium">
              <Sparkles className="h-4 w-4" /> Agent-Builder
            </span>
            <button onClick={cancelNewAgent}
              className="flex items-center gap-1.5 rounded-lg border border-white/15 px-2.5 py-1.5 text-xs text-white/60 hover:text-white transition-colors">
              <X className="h-3 w-3" /> Abbrechen
            </button>
          </>
        )}
        <div className="h-4 w-px bg-white/10" />
        {/* Mode Toggle */}
        {!isNewMode && (
          <div className="flex rounded-lg overflow-hidden border border-white/10">
            <button onClick={() => { setViewMode("resources"); setSelectedNode(null); }}
              className={cn("flex items-center gap-1 px-2.5 py-1.5 text-xs transition-colors",
                viewMode === "resources" ? "bg-indigo-600 text-white" : "bg-zinc-900 text-white/50 hover:text-white")}>
              <Cpu className="h-3 w-3" /> Ressourcen
            </button>
            <button onClick={() => { setViewMode("workflow"); setSelectedNode(null); }}
              className={cn("flex items-center gap-1 px-2.5 py-1.5 text-xs transition-colors",
                viewMode === "workflow" ? "bg-indigo-600 text-white" : "bg-zinc-900 text-white/50 hover:text-white")}>
              <Workflow className="h-3 w-3" /> Workflow
            </button>
          </div>
        )}
        <div className="h-4 w-px bg-white/10" />
        {/* Palette Toggle */}
        <button onClick={() => setShowPalette(p => !p)}
          className="flex items-center gap-1 rounded-lg bg-zinc-900 border border-white/10 px-2.5 py-1.5 text-xs text-white hover:bg-zinc-800 transition-colors">
          <ChevronDown className={cn("h-3 w-3 transition-transform", showPalette && "rotate-180")} /> Palette
        </button>
        {showPalette && (viewMode === "workflow" ? FLOW_PALETTE_ITEMS : PALETTE_ITEMS).map(item => (
          <button key={item.type} onClick={() => addNode(item.type)}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-white/10 px-2.5 py-1.5 text-xs text-white transition-colors">
            <item.icon className={cn("h-3 w-3", item.color)} />
            {item.label}
          </button>
        ))}
        <div className="flex-1" />
        {toast && <span className="text-xs text-indigo-300">{toast}</span>}
        {isNewMode ? (
          <button onClick={createAgent} disabled={creating}
            className="flex items-center gap-1.5 rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
            {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
            {creating ? "Erstelle..." : "Agent erstellen"}
          </button>
        ) : (
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {saving ? t("common.saving") : t("common.save")}
          </button>
        )}
      </div>

      {/* Canvas + Properties */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          {(loading || flowLoading) && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950/60">
              <Loader2 className="h-6 w-6 animate-spin text-white/30" />
            </div>
          )}
          <ReactFlow
            nodes={viewMode === "workflow" ? flowNodes : nodes}
            edges={viewMode === "workflow" ? flowEdges : edges}
            onNodesChange={viewMode === "workflow" ? onFlowNodesChange : onNodesChange}
            onEdgesChange={viewMode === "workflow" ? onFlowEdgesChange : onEdgesChange}
            onConnect={onConnect} nodeTypes={NODE_TYPES}
            colorMode="dark" fitView
            onNodeClick={(_, n) => setSelectedNode(n)}
            onPaneClick={() => setSelectedNode(null)}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.05)" />
            <Controls />
            <MiniMap nodeColor={n => ({
              repository: "#60a5fa", credential: "#fb923c", skill: "#c084fc",
              memory: "#2dd4bf", toolpolicy: "#4ade80", tool: "#22d3ee",
              mcp: "#f472b6", plugin: "#fbbf24", agentprofile: "#818cf8", workflowOverview: "#8b5cf6",
              stepFlow: "#818cf8", sourceFlow: "#34d399", branchFlow: "#fbbf24", endFlow: "#a1a1aa",
            }[n.type ?? ""] ?? "#6366f1")} />
            {viewMode === "resources" && nodes.length === 0 && !loading && !isNewMode && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <p className="text-white/20 text-sm pointer-events-none">Nodes über die Palette hinzufügen und mit dem Agenten verdrahten</p>
              </Panel>
            )}
            {viewMode === "workflow" && flowNodes.length === 0 && !flowLoading && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <div className="text-center space-y-2 pointer-events-none max-w-md">
                  <p className="text-indigo-300/60 text-sm font-medium">Workflow-Editor</p>
                  <p className="text-white/20 text-xs leading-relaxed">
                    Definiere hier den Arbeitsablauf für diesen Agenten.
                    Füge über die Palette Schritte, Quellen, Entscheidungen und ein Ende hinzu.
                    Verbinde die Nodes von links nach rechts.
                    Der Agent befolgt diesen Ablauf bei jeder Aufgabe.
                  </p>
                </div>
              </Panel>
            )}
            {isNewMode && nodes.length === 1 && (
              <Panel position="top-center" style={{ marginTop: 60 }}>
                <div className="text-center space-y-1 pointer-events-none">
                  <p className="text-indigo-300/60 text-sm">Agent-Node auswählen und Eigenschaften rechts konfigurieren</p>
                  <p className="text-white/20 text-xs">Dann Tools, Skills, MCP-Server über die Palette hinzufügen und verbinden</p>
                </div>
              </Panel>
            )}
          </ReactFlow>
        </div>
        <div className="w-64 shrink-0 border-l border-white/10 bg-zinc-900/50 flex flex-col">
          <div className="px-3 py-2 border-b border-white/10">
            <p className="text-[0.65rem] font-bold uppercase tracking-wider text-white/30">Eigenschaften</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <PropertiesPanel
              node={selectedNode}
              onChange={updateNodeData}
              onDelete={deleteNode}
              availableTools={availableTools}
              availableMcp={availableMcp}
              availablePlugins={availablePlugins}
              availableSkills={availableSkills}
              viewMode={viewMode}
              onEditWorkflow={() => { setViewMode("workflow"); setSelectedNode(null); }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export function AgentBlueprintTab() {
  const [agents,  setAgents]  = useState<AgentEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<Record<string, any>>("/agents")
      .then(ad => setAgents(
        Object.entries(ad)
          .filter(([id]) => !id.startsWith("personal_"))
          .map(([id, v]) => ({ id, identity: v.config?.identity || id }))
      ))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <ReactFlowProvider>
      <AgentBlueprintInner agents={agents} />
    </ReactFlowProvider>
  );
}
