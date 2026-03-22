import { useEffect, useMemo, useState } from "react";
import { Bot, FolderKanban, Activity, Cpu, ArrowRight, ShieldCheck, Radar, Workflow } from "lucide-react";
import { api } from "@/lib/api";

export function DashboardPage() {
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    api.health().then(() => setHealthy(true)).catch(() => setHealthy(false));
    api.status().then(setStatus).catch(console.error);
  }, []);

  const runtime = status?.runtime as Record<string, any> | undefined;
  const running = runtime ? Object.values(runtime).filter((a: any) => a.status === "running").length : 0;
  const agents = status?.discovery?.count ?? null;
  const projects = status?.projects?.count ?? null;

  const cards = [
    {
      icon: Activity,
      label: "Core",
      value: healthy === null ? "..." : healthy ? "Online" : "Offline",
      meta: healthy === false ? "API antwortet nicht stabil" : "Healthcheck und API erreichbar",
      state: healthy === false ? "problem" : "ok",
    },
    {
      icon: Bot,
      label: "Agenten",
      value: agents ?? "...",
      meta: "Erkannte Agenten im aktuellen Stand",
      state: "ok",
    },
    {
      icon: FolderKanban,
      label: "Projekte",
      value: projects ?? "...",
      meta: "Aktive Projektdefinitionen",
      state: "ok",
    },
    {
      icon: Cpu,
      label: "Runtime",
      value: running,
      meta: "Gerade laufende Agentenprozesse",
      state: "ok",
    },
  ];

  const healthTone = healthy === false ? "bg-destructive/12 text-destructive" : "status-pill-ok";
  const systemFacts = useMemo(
    () => [
      { label: "Discovery", value: agents ?? "...", note: "Agentprofile geladen" },
      { label: "Projects", value: projects ?? "...", note: "Projektflaechen aktiv" },
      { label: "Runtime", value: running, note: "Workloads in Ausfuehrung" },
    ],
    [agents, projects, running],
  );

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-5 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-3">
              <span className={healthTone + " status-pill"}>
                <span className={"dot " + (healthy === false ? "bg-destructive" : "bg-primary")} />
                {healthy === false ? "Core gestoe rt" : "Core erreichbar"}
              </span>
              <span className="status-pill">
                <Radar className="h-3.5 w-3.5" />
                Design System Baseline in Arbeit
              </span>
            </div>

            <div>
              <h1 className="shell-title">Operations-Dashboard fuer den laufenden OctopOS-Stack</h1>
              <p className="shell-copy mt-3 max-w-2xl">
                Diese Flaeche ist die Referenz fuer die neue App-Shell: klares Lagebild, schnell erfassbare Zustandskarten und
                ein konsistenter visueller Rahmen fuer die Folge-Issues in Projects, Agents und Chat.
              </p>
            </div>
          </div>

          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Workflow className="h-4 w-4 text-primary" />
                Heute im Fokus
              </div>
              <div className="mt-4 space-y-3 text-sm text-muted-foreground">
                <div className="flex items-start justify-between gap-3">
                  <span>App Shell vereinheitlichen</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
                <div className="flex items-start justify-between gap-3">
                  <span>Dashboard als Referenzflaeche modernisieren</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
                <div className="flex items-start justify-between gap-3">
                  <span>Grundlage fuer Projects, Agents und Chat legen</span>
                  <ArrowRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map(({ icon: Icon, label, value, meta, state }) => (
          <div key={label} className="metric-card">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="metric-kicker">{label}</p>
                <p className={"metric-value " + (state === "problem" ? "text-destructive" : "")}>{String(value)}</p>
              </div>
              <div className="rounded-2xl bg-secondary p-3 text-secondary-foreground">
                <Icon className="h-5 w-5" />
              </div>
            </div>
            <p className="metric-meta">{meta}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="section-card">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">Systembild</p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">Kompakte Betriebsansicht</h2>
            </div>
            <span className="status-pill status-pill-ok">Stabil</span>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {systemFacts.map((item) => (
              <div key={item.label} className="rounded-2xl border bg-background/55 p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{item.label}</p>
                <p className="mt-3 text-2xl font-semibold tracking-tight">{String(item.value)}</p>
                <p className="mt-2 text-sm text-muted-foreground">{item.note}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="section-card">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold tracking-tight">Naechste UX-Bloecke</h2>
          </div>
          <ul className="mt-4 space-y-3 text-sm text-muted-foreground">
            <li className="rounded-2xl bg-secondary/55 px-4 py-3">Projects: klarere Status- und Queue-Ansicht</li>
            <li className="rounded-2xl bg-secondary/55 px-4 py-3">Agents: Runtime, Kanaele und Tools staerker gruppieren</li>
            <li className="rounded-2xl bg-secondary/55 px-4 py-3">Chat: besseres Streaming- und Kontextlayout</li>
            <li className="rounded-2xl bg-secondary/55 px-4 py-3">Mobile: Shell und Navigation fuer kleine Screens abschliessen</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
