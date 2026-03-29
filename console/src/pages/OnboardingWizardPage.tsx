import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Circle, ArrowRight, Bot, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface Feature {
  id: string;
  label: string;
  description: string;
  tools: string[];
  defaultOn: boolean;
}

const BASE_TOOLS = ["read_memory", "write_memory", "file_read"];

const GROUP_FEATURES: Record<string, Feature[]> = {
  chatter: [],
  standard: [
    { id: "files",  label: "Dateien schreiben",  description: "Agent kann Dateien erstellen und bearbeiten",         tools: ["file_write"],                                    defaultOn: true  },
    { id: "skills", label: "Eigene Skills",       description: "Agent kann wiederverwendbare Routinen anlegen",       tools: ["create_skill", "list_skills", "delete_skill"],   defaultOn: true  },
  ],
  learning: [
    { id: "files",   label: "Dateien schreiben",  description: "Agent kann Dateien erstellen und bearbeiten",        tools: ["file_write"],                                    defaultOn: true  },
    { id: "skills",  label: "Eigene Skills",       description: "Agent kann wiederverwendbare Routinen anlegen",      tools: ["create_skill", "list_skills", "delete_skill"],   defaultOn: true  },
    { id: "search",  label: "Web-Suche",           description: "Agent kann im Internet recherchieren",              tools: ["web_search"],                                    defaultOn: true  },
  ],
  dev: [
    { id: "files",   label: "Dateien schreiben",  description: "Agent kann Dateien erstellen und bearbeiten",        tools: ["file_write"],                                    defaultOn: true  },
    { id: "skills",  label: "Eigene Skills",       description: "Agent kann wiederverwendbare Routinen anlegen",      tools: ["create_skill", "list_skills", "delete_skill"],   defaultOn: true  },
    { id: "search",  label: "Web-Suche",           description: "Agent kann im Internet recherchieren",              tools: ["web_search"],                                    defaultOn: true  },
    { id: "shell",   label: "Shell-Zugriff",       description: "Agent kann Befehle auf dem System ausführen",       tools: ["shell_exec"],                                    defaultOn: false },
    { id: "git",     label: "Git",                 description: "Agent kann Git-Repositories lesen und schreiben",   tools: ["git_status", "git_log", "git_diff", "git_commit"], defaultOn: false },
  ],
};

const GROUP_LABELS: Record<string, string> = {
  chatter:  "Chatter",
  standard: "Standard",
  learning: "Learning",
  dev:      "Developer",
};

export function OnboardingWizardPage() {
  const navigate = useNavigate();
  const token = localStorage.getItem("hydrahive_token") || "";

  const [group,   setGroup]   = useState<string>("standard");
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState("");
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetch("/api/me/wizard-status", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then((d: { done: boolean; group: string }) => {
        if (d.done) { navigate("/my-agent", { replace: true }); return; }
        const g = d.group || "standard";
        setGroup(g);
        const defaults: Record<string, boolean> = {};
        for (const f of GROUP_FEATURES[g] ?? []) defaults[f.id] = f.defaultOn;
        setEnabled(defaults);
      })
      .catch(() => setError("Wizard-Status konnte nicht geladen werden"))
      .finally(() => setLoading(false));
  }, [navigate, token]);

  function toggle(id: string) {
    setEnabled(prev => ({ ...prev, [id]: !prev[id] }));
  }

  async function handleFinish() {
    setSaving(true); setError("");
    const features = GROUP_FEATURES[group] ?? [];
    const tools = [
      ...BASE_TOOLS,
      ...features.filter(f => enabled[f.id]).flatMap(f => f.tools),
    ];
    try {
      const res = await fetch("/api/me/wizard", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ tools }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      sessionStorage.setItem("hh_wizard_done", "1");
      navigate("/my-agent", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const features = GROUP_FEATURES[group] ?? [];

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="w-full max-w-lg space-y-8">

        {/* Header */}
        <div className="text-center space-y-2">
          <div className="w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto">
            <Bot className="h-7 w-7 text-primary" />
          </div>
          <h1 className="text-2xl font-semibold">Willkommen bei HydraHive</h1>
          <p className="text-sm text-muted-foreground">
            Richte deinen persönlichen Assistenten ein.
            Du hast die Gruppe <span className="font-medium text-foreground">{GROUP_LABELS[group] ?? group}</span>.
          </p>
        </div>

        {/* Feature selection */}
        {features.length > 0 ? (
          <div className="space-y-3">
            <p className="text-sm font-medium">Welche Funktionen möchtest du aktivieren?</p>
            {features.map(f => (
              <button
                key={f.id}
                type="button"
                onClick={() => toggle(f.id)}
                className={cn(
                  "w-full flex items-start gap-4 rounded-xl border p-4 text-left transition-colors",
                  enabled[f.id]
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-accent/50"
                )}
              >
                <div className="flex-shrink-0 mt-0.5">
                  {enabled[f.id]
                    ? <CheckCircle2 className="h-5 w-5 text-primary" />
                    : <Circle className="h-5 w-5 text-muted-foreground" />
                  }
                </div>
                <div>
                  <p className="text-sm font-medium">{f.label}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{f.description}</p>
                </div>
              </button>
            ))}
            <p className="text-xs text-muted-foreground pt-1">
              Memory und Lesezugriff sind immer aktiv. Weitere Funktionen kannst du später unter My Agent anpassen.
            </p>
          </div>
        ) : (
          <div className="rounded-xl border p-5 text-sm text-muted-foreground text-center space-y-1">
            <p className="font-medium text-foreground">Basis-Setup</p>
            <p>Dein Assistent ist mit Chat und Memory eingerichtet.</p>
          </div>
        )}

        {error && <p className="text-sm text-destructive text-center">{error}</p>}

        {/* Finish button */}
        <button
          onClick={handleFinish}
          disabled={saving}
          className="w-full flex items-center justify-center gap-2 px-6 py-3 text-sm font-medium bg-primary text-primary-foreground rounded-xl hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Speichere...</>
            : <><ArrowRight className="h-4 w-4" /> Los geht's</>
          }
        </button>
      </div>
    </div>
  );
}
