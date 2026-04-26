import { useState, useEffect, useCallback } from "react";

const STORAGE_KEY = "hydrahive_dashboard_v1";

export interface WidgetState {
  id: string;
  enabled: boolean;
}

const DEFAULT_WIDGETS: WidgetState[] = [
  { id: "agent-metric", enabled: true },
  { id: "project-metric", enabled: true },
  { id: "runtime-metric", enabled: true },
  { id: "gpu-metric", enabled: true },
  { id: "attention", enabled: true },
  { id: "quick-actions", enabled: true },
  { id: "activity-stream", enabled: true },
  { id: "context-metrics", enabled: true },
  { id: "oauth", enabled: true },
  { id: "codex", enabled: true },
  { id: "minimax", enabled: true },
];

export interface DashboardLayout {
  version: number;
  widgets: WidgetState[];
}

function loadLayout(): WidgetState[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDGETS;
    const parsed: DashboardLayout = JSON.parse(raw);
    if (parsed.version !== 1) return DEFAULT_WIDGETS;
    // Merge: keep new widgets added since last config
    const saved = parsed.widgets;
    const merged = DEFAULT_WIDGETS.map((def) => {
      const found = saved.find((s) => s.id === def.id);
      return found ?? def;
    });
    return merged;
  } catch {
    return DEFAULT_WIDGETS;
  }
}

function saveLayout(widgets: WidgetState[]) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: 1, widgets } satisfies DashboardLayout)
    );
  } catch {
    // ignore
  }
}

export function useWidgetDashboard() {
  const [widgets, setWidgets] = useState<WidgetState[]>(() => loadLayout());
  const [isEditing, setIsEditing] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // Persist on change
  useEffect(() => {
    saveLayout(widgets);
  }, [widgets]);

  const toggleWidget = useCallback((id: string, enabled: boolean) => {
    setWidgets((prev) => prev.map((w) => (w.id === id ? { ...w, enabled } : w)));
  }, []);

  const reorderWidgets = useCallback((newOrder: WidgetState[]) => {
    setWidgets(newOrder);
  }, []);

  const startEdit = useCallback(() => {
    setIsEditing(true);
    setShowSettings(false);
  }, []);

  const cancelEdit = useCallback(() => {
    setIsEditing(false);
  }, []);

  const saveEdit = useCallback(() => {
    setIsEditing(false);
  }, []);

  const openSettings = useCallback(() => {
    setShowSettings(true);
  }, []);

  const closeSettings = useCallback(() => {
    setShowSettings(false);
  }, []);

  const resetToDefault = useCallback(() => {
    setWidgets(DEFAULT_WIDGETS);
  }, []);

  return {
    widgets,
    isEditing,
    showSettings,
    toggleWidget,
    reorderWidgets,
    startEdit,
    cancelEdit,
    saveEdit,
    openSettings,
    closeSettings,
    resetToDefault,
  };
}
