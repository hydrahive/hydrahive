/**
 * QuickstartPage — Epischer interaktiver Quickstart Guide (#375)
 *
 * Drei Persona-Tracks (Einsteiger/Entwickler/Administrator),
 * Auto-Detection des System-Status, Live-Fortschritt, Deep-Links.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  CheckCircle, Circle, ChevronDown, ChevronRight, Rocket, User, Code, Shield,
  Key, Bot, FolderKanban, MessageSquare, Brain, Server, GitBranch, Wrench,
  Archive, Network, Users, BarChart2, ExternalLink, ArrowRight, Sparkles,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import { useCapabilities } from "@/hooks/useCapabilities";
import { cn } from "@/lib/utils";

// ── Types ────────────────────────────────────────────────────────────────────

type Track = "einsteiger" | "entwickler" | "administrator";

interface StepDef {
  id: string;
  title: string;
  description: string;
  icon: React.ElementType;
  checkFn: (ctx: SysCtx) => boolean;
  actionLabel: string;
  actionRoute?: string;
  detail?: string;
}

interface SysCtx {
  llmConfigured: boolean;
  agentsCount: number;
  projectsCount: number;
  sessionsCount: number;
  caps: Record<string, { active?: boolean; configured?: boolean; installed?: boolean }>;
  doctorChecks: Record<string, string>;
  userCount: number;
}

// ── Step Definitions ─────────────────────────────────────────────────────────

const STEPS_EINSTEIGER: StepDef[] = [
  {
    id: "step-llm",
    title: "LLM einrichten",
    description: "API-Schlüssel für Claude oder GPT hinterlegen — die Grundlage für alles.",
    icon: Key,
    checkFn: ctx => ctx.llmConfigured,
    actionLabel: "API-Key einrichten",
    actionRoute: "/settings?tab=llm",
    detail: "Gehe zu Einstellungen → LLM und trage deinen Anthropic- oder OpenAI-Schlüssel ein. Du findest ihn unter console.anthropic.com oder platform.openai.com.",
  },
  {
    id: "step-agent",
    title: "Deinen Agent kennenlernen",
    description: "Jeder User hat einen persönlichen Agent — besuche ihn und sag Hallo.",
    icon: Bot,
    checkFn: ctx => ctx.agentsCount > 0,
    actionLabel: "Zum Agent",
    actionRoute: "/my-agent",
    detail: "Dein persönlicher Agent ist dein KI-Assistent. Er hat Memory, Skills und kann Tools nutzen. Schreib ihm einfach eine Nachricht!",
  },
  {
    id: "step-project",
    title: "Erstes Projekt erstellen",
    description: "Projekte bündeln Agenten zu Teams — erstelle dein erstes.",
    icon: FolderKanban,
    checkFn: ctx => ctx.projectsCount > 0,
    actionLabel: "Projekt erstellen",
    actionRoute: "/projects/new",
    detail: "Ein Projekt hat einen Boss-Agent und optional Worker. Der Boss koordiniert, die Worker erledigen Teilaufgaben. Starte mit einem einfachen Projekt.",
  },
  {
    id: "step-chat",
    title: "Ersten Chat starten",
    description: "Schick deinem Agent eine Nachricht und sieh zu wie er antwortet.",
    icon: MessageSquare,
    checkFn: ctx => ctx.sessionsCount > 0,
    actionLabel: "Chat starten",
    actionRoute: "/my-agent",
    detail: "Tippe eine Frage oder Aufgabe ein. Dein Agent nutzt automatisch die ihm zugewiesenen Tools. Probier z.B. 'Welche Tools hast du?' oder 'Schreib mir ein Python-Skript'.",
  },
  {
    id: "step-memory",
    title: "Memory nutzen",
    description: "Dein Agent merkt sich Dinge — nutze /remember um ihm etwas beizubringen.",
    icon: Brain,
    checkFn: () => false, // Schwer auto-detektierbar, User muss es probieren
    actionLabel: "Ausprobieren",
    actionRoute: "/my-agent",
    detail: "Schreib im Chat '/remember Mein Name ist Max' — dein Agent speichert das in seiner Memory und erinnert sich in zukünftigen Gesprächen daran.",
  },
];

const STEPS_ENTWICKLER: StepDef[] = [
  ...STEPS_EINSTEIGER,
  {
    id: "step-server",
    title: "Remote-Server verbinden",
    description: "Verbinde einen Server per SSH — dein Agent kann dann dort Befehle ausführen.",
    icon: Server,
    checkFn: ctx => {
      const c = ctx.caps["server"] || ctx.caps["server_shell"];
      return !!(c?.configured || c?.active);
    },
    actionLabel: "Server einrichten",
    actionRoute: "/agents?tab=servers",
    detail: "Unter Agents → Server kannst du SSH-Server hinzufügen. HydraHive generiert einen SSH-Key — trage den Public Key auf dem Zielserver in ~/.ssh/authorized_keys ein. Dein Agent nutzt dann server_shell statt manuelles SSH.",
  },
  {
    id: "step-git",
    title: "Git-Repository anbinden",
    description: "Verbinde Gitea oder GitHub damit dein Agent Code lesen und schreiben kann.",
    icon: GitBranch,
    checkFn: ctx => {
      const gitea = ctx.caps["gitea"];
      const github = ctx.caps["github"];
      return !!(gitea?.configured || github?.configured);
    },
    actionLabel: "Git einrichten",
    actionRoute: "/settings?tab=gitea",
    detail: "Gehe zu Einstellungen → Gitea oder GitHub. Trage deine Server-URL und einen API-Token ein. Danach kann dein Agent Issues erstellen, Code lesen und Commits machen.",
  },
  {
    id: "step-skills",
    title: "Custom Skills erstellen",
    description: "Bringe deinem Agent wiederverwendbare Routinen bei.",
    icon: Wrench,
    checkFn: () => false,
    actionLabel: "Skills entdecken",
    actionRoute: "/hub?tab=skill-packages",
    detail: "Skills sind gespeicherte Anleitungen die dein Agent bei passenden Aufgaben automatisch aktiviert. Erstelle einen Skill unter My Agent → Skills oder installiere fertige aus dem Hub.",
  },
];

const STEPS_ADMIN: StepDef[] = [
  ...STEPS_ENTWICKLER,
  {
    id: "step-backup",
    title: "Backup einrichten",
    description: "Automatische Backups schützen deine Konfiguration und Daten.",
    icon: Archive,
    checkFn: ctx => ctx.doctorChecks["backup"] === "ok",
    actionLabel: "Backup konfigurieren",
    actionRoute: "/settings?tab=backup",
    detail: "Unter Einstellungen → Backup kannst du manuelle Backups erstellen oder den automatischen Cron-Job (03:00 Uhr täglich) aktivieren. Backups enthalten Agents, Projekte, Memory und Einstellungen.",
  },
  {
    id: "step-vpn",
    title: "VPN konfigurieren",
    description: "Tailscale verbindet deine Server sicher über das Internet.",
    icon: Network,
    checkFn: ctx => {
      const vpn = ctx.caps["tailscale"] || ctx.caps["vpn"];
      return !!(vpn?.active);
    },
    actionLabel: "VPN einrichten",
    actionRoute: "/settings?tab=vpn",
    detail: "Tailscale erstellt ein privates Netzwerk zwischen deinen Servern. Installiere Tailscale, generiere einen Auth-Key im Tailscale Dashboard und trage ihn hier ein.",
  },
  {
    id: "step-users",
    title: "Weitere User einladen",
    description: "Lade Teammitglieder ein — jeder bekommt einen eigenen Agent.",
    icon: Users,
    checkFn: ctx => ctx.userCount > 1,
    actionLabel: "User verwalten",
    actionRoute: "/usermanagement",
    detail: "Unter User-Verwaltung kannst du neue Accounts anlegen oder Einladungslinks generieren. Jeder User bekommt automatisch einen persönlichen Agent mit eigenem Memory.",
  },
  {
    id: "step-monitoring",
    title: "Monitoring & Audit",
    description: "Behalte Token-Verbrauch, Audit-Logs und Systemgesundheit im Blick.",
    icon: BarChart2,
    checkFn: () => false,
    actionLabel: "Dashboard öffnen",
    actionRoute: "/dashboard",
    detail: "Das Dashboard zeigt Token-Usage, aktive Sessions und System-Health. Unter Audit-Logs siehst du wer wann was gemacht hat. Der Doctor-Check prüft ob alle Services korrekt laufen.",
  },
];

const TRACKS: { id: Track; label: string; icon: React.ElementType; steps: StepDef[]; desc: string }[] = [
  { id: "einsteiger",    label: "Einsteiger",    icon: User,   steps: STEPS_EINSTEIGER,  desc: "Die Basics — in 5 Schritten startklar" },
  { id: "entwickler",    label: "Entwickler",    icon: Code,   steps: STEPS_ENTWICKLER,  desc: "Server, Git & Skills — volle Power" },
  { id: "administrator", label: "Administrator", icon: Shield, steps: STEPS_ADMIN,       desc: "Backup, VPN, Users — alles unter Kontrolle" },
];

// ── Data Hook ────────────────────────────────────────────────────────────────

function useQuickstartData() {
  const { capabilities, loading: capsLoading } = useCapabilities();
  const [data, setData] = useState<{
    llmConfigured: boolean; agentsCount: number; projectsCount: number;
    sessionsCount: number; doctorChecks: Record<string, string>; userCount: number;
  }>({ llmConfigured: false, agentsCount: 0, projectsCount: 0, sessionsCount: 0, doctorChecks: {}, userCount: 0 });
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<any>("/setup/status").catch(() => ({})),
      api.get<any>("/status").catch(() => ({})),
      api.get<any>("/admin/doctor").catch(() => ({ checks: [] })),
      api.get<any>("/admin/users").catch(() => ({ users: [] })),
    ]).then(([setup, status, doctor, users]) => {
      const checks: Record<string, string> = {};
      for (const c of (doctor.checks || [])) checks[c.name?.toLowerCase() || ""] = c.status;
      setData({
        llmConfigured: setup.llm_configured ?? false,
        agentsCount: status.discovery?.count ?? 0,
        projectsCount: status.projects?.count ?? 0,
        sessionsCount: (status.sessions?.active_projects || []).length,
        doctorChecks: checks,
        userCount: (users.users || []).length,
      });
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const ctx: SysCtx = useMemo(() => ({
    ...data,
    caps: capabilities as any || {},
  }), [data, capabilities]);

  return { ctx, loading: loading || capsLoading, refresh };
}

// ── ProgressRing ─────────────────────────────────────────────────────────────

function ProgressRing({ percent, size = 120 }: { percent: number; size?: number }) {
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;
  const color = percent === 100 ? "text-green-500" : percent >= 60 ? "text-primary" : percent >= 30 ? "text-amber-500" : "text-muted-foreground";

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none"
          stroke="currentColor" strokeWidth={stroke} className="text-muted/30" />
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none"
          stroke="currentColor" strokeWidth={stroke} className={cn(color, "transition-all duration-1000 ease-out")}
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={cn("text-2xl font-bold", color)}>{percent}%</span>
        <span className="text-[10px] text-muted-foreground">eingerichtet</span>
      </div>
    </div>
  );
}

// ── HeroSection ──────────────────────────────────────────────────────────────

function HeroSection({ track, setTrack, percent, onRefresh }: {
  track: Track; setTrack: (t: Track) => void; percent: number; onRefresh: () => void;
}) {
  return (
    <div className="rounded-2xl border bg-gradient-to-br from-primary/5 via-background to-primary/3 p-8 space-y-6">
      <div className="flex flex-col sm:flex-row items-center gap-6">
        <ProgressRing percent={percent} />
        <div className="text-center sm:text-left space-y-2">
          <div className="flex items-center gap-2 justify-center sm:justify-start">
            <Rocket className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">Quickstart Guide</h1>
          </div>
          <p className="text-muted-foreground">
            {percent === 100
              ? "Dein HydraHive ist vollständig eingerichtet!"
              : "Folge den Schritten um dein HydraHive einzurichten."}
          </p>
          <button onClick={onRefresh}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className="h-3 w-3" /> Status aktualisieren
          </button>
        </div>
      </div>

      {/* Persona Picker */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {TRACKS.map(t => (
          <button key={t.id} onClick={() => setTrack(t.id)}
            className={cn(
              "flex items-center gap-3 rounded-xl border p-4 text-left transition-all",
              track === t.id
                ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                : "border-border hover:bg-accent/50 hover:border-muted-foreground/30"
            )}>
            <div className={cn(
              "flex h-10 w-10 items-center justify-center rounded-lg shrink-0",
              track === t.id ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"
            )}>
              <t.icon className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="font-semibold text-sm">{t.label}</p>
              <p className="text-xs text-muted-foreground">{t.desc}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── StepCard ─────────────────────────────────────────────────────────────────

function StepCard({ step, index, done, isNext, isLast }: {
  step: StepDef; index: number; done: boolean; isNext: boolean; isLast: boolean;
}) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const Icon = step.icon;
  const Chevron = expanded ? ChevronDown : ChevronRight;

  // Auto-expand via hash deep-link
  const { hash } = useLocation();
  useEffect(() => {
    if (hash === `#${step.id}`) {
      setExpanded(true);
      setTimeout(() => document.getElementById(step.id)?.scrollIntoView({ behavior: "smooth" }), 100);
    }
  }, [hash, step.id]);

  return (
    <div id={step.id} className="flex gap-4 scroll-mt-20">
      {/* Timeline */}
      <div className="flex flex-col items-center shrink-0 w-10">
        <div className={cn(
          "flex h-10 w-10 items-center justify-center rounded-full border-2 transition-all",
          done ? "border-green-500 bg-green-500/10" :
          isNext ? "border-primary bg-primary/10 animate-pulse" :
          "border-muted bg-muted/30"
        )}>
          {done
            ? <CheckCircle className="h-5 w-5 text-green-500" />
            : <span className={cn("text-sm font-bold", isNext ? "text-primary" : "text-muted-foreground")}>{index + 1}</span>
          }
        </div>
        {!isLast && (
          <div className={cn("w-px flex-1 min-h-[24px]", done ? "bg-green-500/40" : "bg-border")} />
        )}
      </div>

      {/* Content */}
      <div className={cn(
        "flex-1 rounded-xl border p-4 mb-3 transition-all",
        done ? "bg-green-500/[0.02] border-green-500/20" :
        isNext ? "bg-primary/[0.02] border-primary/20 shadow-sm" :
        "bg-card border-border"
      )}>
        <div className="flex items-start gap-3">
          <Icon className={cn("h-5 w-5 mt-0.5 shrink-0", done ? "text-green-500" : isNext ? "text-primary" : "text-muted-foreground")} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className={cn("font-semibold text-sm", done && "line-through text-muted-foreground")}>{step.title}</h3>
              {done && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/10 text-green-600 font-medium">Erledigt</span>}
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{step.description}</p>

            {/* Actions */}
            {!done && (
              <div className="flex items-center gap-2 mt-3">
                {step.actionRoute && (
                  <button onClick={() => navigate(step.actionRoute!)}
                    className={cn(
                      "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors",
                      isNext
                        ? "bg-primary text-primary-foreground hover:bg-primary/90"
                        : "border hover:bg-muted"
                    )}>
                    {step.actionLabel} <ArrowRight className="h-3 w-3" />
                  </button>
                )}
              </div>
            )}

            {/* Expandable Detail */}
            {step.detail && (
              <>
                <button onClick={() => setExpanded(!expanded)}
                  className="flex items-center gap-1 mt-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
                  <Chevron className="h-3 w-3" />
                  {expanded ? "Weniger" : "Mehr erfahren"}
                </button>
                {expanded && (
                  <div className="mt-3 p-3 rounded-lg bg-muted/30 text-xs text-muted-foreground leading-relaxed border border-border/50">
                    {step.detail}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Celebration ──────────────────────────────────────────────────────────────

function CelebrationBanner() {
  return (
    <div className="rounded-2xl border border-green-500/30 bg-gradient-to-r from-green-500/10 via-emerald-500/10 to-teal-500/10 p-6 text-center space-y-3">
      <div className="flex justify-center">
        <div className="relative">
          <Sparkles className="h-12 w-12 text-green-500 animate-bounce" />
          <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-amber-400 animate-ping" />
          <div className="absolute -bottom-1 -left-1 h-2 w-2 rounded-full bg-primary animate-ping" style={{ animationDelay: "0.3s" }} />
        </div>
      </div>
      <h2 className="text-xl font-bold text-green-600 dark:text-green-400">
        Geschafft!
      </h2>
      <p className="text-sm text-muted-foreground">
        Dein HydraHive ist vollständig eingerichtet. Alle Systeme laufen.
      </p>
      <a href="/dashboard"
        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-green-600 text-white rounded-xl hover:bg-green-700 transition-colors">
        Zum Dashboard <ExternalLink className="h-3.5 w-3.5" />
      </a>
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export function QuickstartPage() {
  const { ctx, loading, refresh } = useQuickstartData();
  const [track, setTrack] = useState<Track>(() =>
    (localStorage.getItem("hh_quickstart_track") as Track) || "einsteiger"
  );

  useEffect(() => { localStorage.setItem("hh_quickstart_track", track); }, [track]);

  const steps = TRACKS.find(t => t.id === track)!.steps;
  const completed = steps.filter(s => s.checkFn(ctx)).length;
  const percent = steps.length ? Math.round((completed / steps.length) * 100) : 0;

  // Finde den ersten unerledigten Step
  const firstIncompleteIdx = steps.findIndex(s => !s.checkFn(ctx));

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex flex-col items-center gap-3">
          <Rocket className="h-8 w-8 text-primary animate-pulse" />
          <p className="text-sm text-muted-foreground">Lade System-Status...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6 pb-20">
      <HeroSection track={track} setTrack={setTrack} percent={percent} onRefresh={refresh} />

      {/* Steps */}
      <div className="space-y-0">
        {steps.map((step, i) => (
          <StepCard
            key={step.id}
            step={step}
            index={i}
            done={step.checkFn(ctx)}
            isNext={i === firstIncompleteIdx}
            isLast={i === steps.length - 1}
          />
        ))}
      </div>

      {/* Celebration */}
      {percent === 100 && <CelebrationBanner />}
    </div>
  );
}
