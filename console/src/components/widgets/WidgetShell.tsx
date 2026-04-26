import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { cn } from "@/lib/utils";
import { X, GripVertical } from "lucide-react";

export interface WidgetProps {
  widgetId: string;
  isEditing?: boolean;
  className?: string;
  children?: React.ReactNode;
}

export function WidgetShell({
  widgetId,
  isEditing = false,
  className = "",
  children,
}: WidgetProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: widgetId, disabled: !isEditing });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "relative group rounded-xl border bg-card overflow-hidden",
        isDragging && "opacity-50 z-50 shadow-2xl ring-2 ring-primary",
        !isDragging && isEditing && "hover:ring-1 hover:ring-primary/40",
        className
      )}
    >
      {/* Drag Handle — only visible in edit mode */}
      {isEditing && (
        <div
          {...attributes}
          {...listeners}
          className="absolute top-2 left-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground"
        >
          <GripVertical className="h-4 w-4" />
        </div>
      )}

      {/* Remove button — only in edit mode */}
      {isEditing && (
        <button
          type="button"
          className="absolute top-2 right-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-md bg-destructive/80 text-destructive-foreground hover:bg-destructive"
          // onRemove handled by parent via widgetId
        >
          <X className="h-3 w-3" />
        </button>
      )}

      {children}
    </div>
  );
}
