import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import {
  Cpu, GitBranch, Github, Network, Mail, Key, Users, Server, Globe, Bell,
  Plug, Link, Mic, Map, CheckCircle, XCircle, Webhook,
} from "lucide-react";

interface Surface {
  id: string;
  label: string;
  description: string;
  icon: string;
  ui_path: string;
  config_file: string;
  owner: string;
  status: "configured" | "unconfigured" | "partial";
}

interface ConfigMapResponse {
  surfaces: Surface[];
  summary: { configured: number; unconfigured: number; total: number };
}

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  cpu: Cpu, "git-branch": GitBranch, github: Github, network: Network,
  mail: Mail, key: Key, users: Users, server: Server, globe: Globe,
  bell: Bell, plug: Plug, link: Link, mic: Mic, webhook: Webhook,
};

function SurfaceIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICON_MAP[name] ?? Plug;
  return <Icon className={className} />;
}

export function ConfigMapPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<ConfigMapResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<ConfigMapResponse>("/config/map")
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : "Fehler"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 text-muted-foreground">Lade...</div>;
  if (error) return <div className="p-6 text-destructive">{error}</div>;
  if (!data) return null;

  const { surfaces, summary } = data;

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Map className="h-5 w-5" /> Konfigurationslandkarte
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            Alle Einstellungen auf einen Blick — klicke auf eine Karte um direkt dorthin zu springen.
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="flex items-center gap-1.5 text-green-600">
            <CheckCircle className="h-4 w-4" /> {summary.configured} konfiguriert
          </span>
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <XCircle className="h-4 w-4" /> {summary.unconfigured} offen
          </span>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {surfaces.map(s => (
          <button
            key={s.id}
            onClick={() => navigate(s.ui_path)}
            className={`text-left rounded-xl border p-4 transition-all hover:shadow-md hover:border-primary/30 ${
              s.status === "configured"
                ? "bg-card"
                : "bg-muted/30 border-dashed"
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <div className={`rounded-lg p-2 ${
                  s.status === "configured" ? "bg-green-500/10" : "bg-muted"
                }`}>
                  <SurfaceIcon name={s.icon} className={`h-4 w-4 ${
                    s.status === "configured" ? "text-green-600" : "text-muted-foreground"
                  }`} />
                </div>
                <div>
                  <p className="text-sm font-medium">{s.label}</p>
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{s.description}</p>
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between mt-3 pt-2 border-t border-border/50">
              <span className="text-[10px] font-mono text-muted-foreground/60 truncate max-w-[60%]">
                {s.config_file}
              </span>
              <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                s.status === "configured"
                  ? "bg-green-500/10 text-green-600"
                  : s.status === "partial"
                  ? "bg-amber-500/10 text-amber-600"
                  : "bg-muted text-muted-foreground"
              }`}>
                {s.status === "configured" ? "Aktiv" : s.status === "partial" ? "Teilweise" : "Nicht konfiguriert"}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
