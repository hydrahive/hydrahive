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
    // Merge: saved order zuerst, neue Widgets anhängen
    const saved = parsed.widgets;
    const knownIds = new Set(DEFAULT_WIDGETS.map((d) => d.id));
    const ordered = saved.filter((s) => knownIds.has(s.id));
    const savedIds = new Set(ordered.map((s) => s.id));
    const newWidgets = DEFAULT_WIDGETS.filter((d) => !savedIds.has(d.id));
    return [...ordered, ...newWidgets];
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
