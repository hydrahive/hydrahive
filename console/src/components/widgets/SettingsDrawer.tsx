import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface WidgetConfig {
  id: string;
  label: string;
  enabled: boolean;
}

interface SettingsDrawerProps {
  widgets: WidgetConfig[];
  onToggle: (id: string, enabled: boolean) => void;
  onClose: () => void;
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
        checked ? "bg-primary" : "bg-muted"
      )}
    >
      <span
        className={cn(
          "inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform",
          checked ? "translate-x-5" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

export function SettingsDrawer({ widgets, onToggle, onClose }: SettingsDrawerProps) {
  return (
    <div className="fixed inset-y-0 right-0 z-50 w-64 bg-background border-l shadow-xl flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div>
          <h3 className="text-sm font-semibold">Dashboard Widgets</h3>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            Toggle widgets on/off
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-md hover:bg-muted transition-colors"
        >
          <X className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>

      {/* Widget List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {widgets.map((w) => (
          <div
            key={w.id}
            className="flex items-center gap-3 py-2 px-2 rounded-lg hover:bg-muted/50"
          >
            <span className="text-xs text-muted-foreground cursor-grab active:cursor-grabbing select-none">
              ⋮⋮
            </span>
            <span className="flex-1 text-xs font-medium truncate">{w.label}</span>
            <Toggle
              checked={w.enabled}
              onChange={(checked) => onToggle(w.id, checked)}
            />
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="p-4 border-t">
        <button
          type="button"
          onClick={onClose}
          className="w-full rounded-md border bg-card px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  );
}
