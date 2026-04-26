import {
  SortableContext,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import { WidgetShell, WidgetProps } from "./WidgetShell";
import { cn } from "@/lib/utils";

export interface DashboardWidget {
  id: string;
  span?: 1 | 2 | 3 | 4;
  rowSpan?: 1 | 2;
}

interface DashboardGridProps {
  widgets: Array<{ id: string; component: React.ComponentType<WidgetProps> }>;
  widgetConfigs: Record<string, DashboardWidget>;
  isEditing?: boolean;
  onRemoveWidget?: (id: string) => void;
  className?: string;
}

export function DashboardGrid({
  widgets,
  widgetConfigs,
  isEditing = false,
  className = "",
}: DashboardGridProps) {
  const sortedIds = widgets.map((w) => w.id);

  return (
    <SortableContext items={sortedIds} strategy={rectSortingStrategy}>
      <div
        className={cn(
          "grid gap-3",
          // 4-column base grid
          "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4",
          className
        )}
      >
        {widgets.map(({ id, component: Component }) => {
          const cfg = widgetConfigs[id] ?? {};
          const span = cfg.span ?? 2;
          const rowSpan = cfg.rowSpan ?? 1;

          return (
            <div
              key={id}
              className={cn(
                // Column span
                span === 1 && "col-span-1",
                span === 2 && "col-span-2",
                span === 3 && "col-span-3",
                span === 4 && "col-span-4",
                // Row span
                rowSpan === 2 && "row-span-2"
              )}
            >
              <WidgetShell
                widgetId={id}
                isEditing={isEditing}
                className="h-full"
              >
                <Component widgetId={id} isEditing={isEditing} />
              </WidgetShell>
            </div>
          );
        })}
      </div>
    </SortableContext>
  );
}
