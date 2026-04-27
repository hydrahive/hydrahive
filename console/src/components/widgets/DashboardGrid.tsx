import { useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { WidgetShell } from "./WidgetShell";
import { cn } from "@/lib/utils";
import type { WidgetConfig } from "./types";
import type { WidgetState } from "./useWidgetDashboard";

export interface WidgetComponent {
  id: string;
  component: React.ComponentType<{
    widgetId: string;
    isEditing?: boolean;
    className?: string;
  }>;
}

interface DashboardGridProps {
  widgets: WidgetComponent[];
  widgetConfigs: Record<string, WidgetConfig>;
  widgetStates: WidgetState[];
  isEditing: boolean;
  onReorder: (newOrder: WidgetState[]) => void;
  onRemoveWidget: (id: string) => void;
  className?: string;
}

export function DashboardGrid({
  widgets,
  widgetConfigs,
  widgetStates,
  isEditing,
  onReorder,
  onRemoveWidget,
  className,
}: DashboardGridProps) {
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor)
  );

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    // Indices from widgetStates (canonical order)
    const oldIndex = widgetStates.findIndex((w) => w.id === active.id);
    const newIndex = widgetStates.findIndex((w) => w.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = arrayMove(widgetStates, oldIndex, newIndex);
    onReorder(reordered);
  }

  // widgetStates is the canonical order; sort widgets to match
  const sortedIds = widgetStates.filter((w) => w.enabled).map((w) => w.id);
  const enabledIds = new Set(widgetStates.filter((w) => w.enabled).map((w) => w.id));
  // Sort WidgetComponents to match widgetStates order (not static widgets order)
  const visibleWidgets = widgetStates
    .filter((s) => enabledIds.has(s.id))
    .map((s) => widgets.find((w) => w.id === s.id))
    .filter(Boolean) as WidgetComponent[];
  const activeWidget = activeId ? visibleWidgets.find((w) => w.id === activeId) : null;

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={sortedIds} strategy={rectSortingStrategy}>
        <div
          className={cn(
            "grid gap-3",
            "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4",
            className
          )}
        >
          {visibleWidgets.map(({ id, component: Component }) => {
            const cfg = widgetConfigs[id] ?? {};
            const span = cfg.span ?? 2;
            const rowSpan = cfg.rowSpan ?? 1;
            const isBeingDragged = activeId === id;

            return (
              <div
                key={id}
                className={cn(
                  span === 1 && "col-span-1",
                  span === 2 && "col-span-2",
                  span === 3 && "col-span-3",
                  span === 4 && "col-span-4",
                  rowSpan === 2 && "row-span-2",
                  isBeingDragged && "opacity-30"
                )}
              >
                <WidgetShell
                  widgetId={id}
                  isEditing={isEditing}
                  className="h-full"
                  onRemove={() => onRemoveWidget(id)}
                >
                  <Component widgetId={id} isEditing={isEditing} />
                </WidgetShell>
              </div>
            );
          })}
        </div>

        <DragOverlay dropAnimation={null}>
          {activeWidget ? (
            <div className="w-80 rounded-xl border bg-card overflow-hidden shadow-2xl ring-2 ring-primary">
              <activeWidget.component
                widgetId={activeWidget.id}
                isEditing={isEditing}
              />
            </div>
          ) : null}
        </DragOverlay>
      </SortableContext>
    </DndContext>
  );
}
