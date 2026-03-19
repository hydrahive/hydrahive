import { useEffect, useState } from "react";
import { Bot, FolderKanban, Activity, Cpu } from "lucide-react";
import { api } from "@/lib/api";
export function DashboardPage() {
  const [status, setStatus]   = useState<Record<string,any>|null>(null);
  const [healthy, setHealthy] = useState<boolean|null>(null);
  useEffect(() => {
    api.health().then(()=>setHealthy(true)).catch(()=>setHealthy(false));
    api.status().then(setStatus).catch(console.error);
  }, []);
  const running = status?.runtime
    ? Object.values(status.runtime as Record<string,any>).filter((a:any)=>a.status==="running").length : 0;
  const cards = [
    { icon:Activity,     label:"Core",     value:healthy===null?"...":healthy?"Online":"Offline", ok:healthy!==false },
    { icon:Bot,          label:"Agenten",  value:status?.discovery?.count??"...", ok:true },
    { icon:FolderKanban, label:"Projekte", value:status?.projects?.count??"...",  ok:true },
    { icon:Cpu,          label:"Laufend",  value:running,                         ok:true },
  ];
  return (
    <div className="p-6 space-y-6">
      <div><h1 className="text-xl font-semibold">Dashboard</h1><p className="text-sm text-muted-foreground">System-Uebersicht</p></div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {cards.map(({icon:Icon, label, value, ok}) => (
          <div key={label} className="bg-card border rounded-lg p-4 space-y-2">
            <div className="flex items-center gap-2 text-muted-foreground">
              <Icon className="h-4 w-4"/><span className="text-xs font-medium uppercase tracking-wide">{label}</span>
            </div>
            <p className={`text-2xl font-semibold ${ok?"":"text-destructive"}`}>{String(value)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
