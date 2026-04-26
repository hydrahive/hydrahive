import { Pencil, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";

export function EditBar({
  isEditing,
  onStartEdit,
  onSave,
  onCancel,
  className,
}: {
  isEditing: boolean;
  onStartEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      {isEditing ? (
        <>
          <button
            type="button"
            onClick={onCancel}
            className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted transition-colors"
          >
            <X className="h-3 w-3" />
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Check className="h-3 w-3" />
            Save
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={onStartEdit}
          className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted transition-colors"
        >
          <Pencil className="h-3 w-3" />
          Layout editieren
        </button>
      )}
    </div>
  );
}
