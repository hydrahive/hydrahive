import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
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
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor)
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = widgets.findIndex((w) => w.id === active.id);
    const newIndex = widgets.findIndex((w) => w.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = arrayMove(widgetStates, oldIndex, newIndex);
    onReorder(reordered);
  }

  const sortedIds = widgets.map((w) => w.id);
  const enabledIds = new Set(widgetStates.filter((w) => w.enabled).map((w) => w.id));
  const visibleWidgets = widgets.filter((w) => enabledIds.has(w.id));

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
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

            return (
              <div
                key={id}
                className={cn(
                  span === 1 && "col-span-1",
                  span === 2 && "col-span-2",
                  span === 3 && "col-span-3",
                  span === 4 && "col-span-4",
                  rowSpan === 2 && "row-span-2"
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
      </SortableContext>
    </DndContext>
  );
}
