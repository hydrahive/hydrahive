import { Cpu, Radar } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "./WidgetShell";
import type { GpuEntry } from "@/lib/api";

type Props = WidgetProps & {
  gpuList: GpuEntry[];
  runningHeartbeats: number;
};

export function GPUMetricWidget({ gpuList, runningHeartbeats, className }: Props) {
  const hottestGpu =
    gpuList.length > 0
      ? gpuList.reduce((a, b) => ((a.temp_c ?? 0) > (b.temp_c ?? 0) ? a : b))
      : null;

  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-2 flex items-center gap-2",
        className
      )}
    >
      <div
        className="rounded-lg p-1.5 shrink-0"
        style={{ background: "hsl(28 90% 58% / 0.15)" }}
      >
        {gpuList.length > 0 ? (
          <Cpu
            className="h-4 w-4"
            style={{ color: "hsl(var(--candy-amber))" }}
          />
        ) : (
          <Radar
            className="h-4 w-4"
            style={{ color: "hsl(var(--candy-amber))" }}
          />
        )}
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground truncate">
          {gpuList.length > 0 ? "GPU Temp" : "Heartbeats"}
        </p>
        <p
          className="text-xl font-bold"
          style={{ color: "hsl(var(--candy-amber))" }}
        >
          {gpuList.length > 0
            ? `${hottestGpu?.temp_c ?? "-"}°C`
            : runningHeartbeats}
        </p>
      </div>
    </div>
  );
}
