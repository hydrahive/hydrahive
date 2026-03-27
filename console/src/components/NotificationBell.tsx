import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, Check, CheckCheck, Trash2, X } from "lucide-react";
import { api, AppNotification } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

const TYPE_ICONS: Record<string, string> = {
  task_done:      "✓",
  task_failed:    "✗",
  schedule_run:   "⏰",
  agent_warning:  "⚠",
  system:         "ℹ",
};

export function NotificationBell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open,    setOpen]    = useState(false);
  const [items,   setItems]   = useState<AppNotification[]>([]);
  const [unread,  setUnread]  = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  // Initiales Laden + Unread-Count
  async function loadAll() {
    try {
      const [nRes, cRes] = await Promise.all([api.notifications(), api.unreadCount()]);
      setItems(nRes.notifications);
      setUnread(cRes.count);
    } catch { /* silent */ }
  }

  useEffect(() => {
    loadAll();
    // SSE-Stream für Live-Updates
    const token = localStorage.getItem("hydrahive_token") || "";
    const es = new EventSource(`/api/notifications/stream?token=${token}`);
    es.onmessage = (e) => {
      try {
        const n: AppNotification = JSON.parse(e.data);
        if (n.type === "heartbeat") return;
        setItems(prev => [n, ...prev].slice(0, 50));
        setUnread(prev => prev + 1);
      } catch { /* ignore parse errors */ }
    };
    return () => es.close();
  }, []);

  // Klick außerhalb schließt Dropdown
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  async function handleMarkRead(id: string) {
    await api.markRead(id);
    setItems(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    setUnread(prev => Math.max(0, prev - 1));
  }

  async function handleMarkAllRead() {
    await api.markAllRead();
    setItems(prev => prev.map(n => ({ ...n, read: true })));
    setUnread(0);
  }

  async function handleDelete(id: string, wasRead: boolean) {
    await api.deleteNotif(id);
    setItems(prev => prev.filter(n => n.id !== id));
    if (!wasRead) setUnread(prev => Math.max(0, prev - 1));
  }

  function handleClick(n: AppNotification) {
    if (!n.read) handleMarkRead(n.id);
    if (n.link) { navigate(n.link); setOpen(false); }
  }

  const visible = items.slice(0, 10);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="relative flex items-center justify-center rounded-2xl border border-border/60 bg-card/70 p-2 text-muted-foreground shadow-sm hover:text-foreground transition-colors"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[0.6rem] font-bold text-primary-foreground">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-80 rounded-2xl border border-border bg-card shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <span className="text-sm font-semibold">{t("notifications.title")}</span>
            <div className="flex items-center gap-1">
              {unread > 0 && (
                <button
                  onClick={handleMarkAllRead}
                  title={t("notifications.markAllRead")}
                  className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                >
                  <CheckCheck className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Liste */}
          <div className="max-h-96 overflow-y-auto">
            {visible.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                {t("notifications.empty")}
              </p>
            ) : (
              visible.map(n => (
                <div
                  key={n.id}
                  className={cn(
                    "group flex gap-3 px-4 py-3 border-b border-border/50 last:border-0 transition-colors",
                    !n.read && "bg-primary/5",
                    n.link && "cursor-pointer hover:bg-accent/50",
                  )}
                  onClick={() => handleClick(n)}
                >
                  <span className="mt-0.5 flex-shrink-0 text-sm">{TYPE_ICONS[n.type] ?? "●"}</span>
                  <div className="min-w-0 flex-1">
                    <p className={cn("truncate text-sm", !n.read && "font-medium")}>{n.title}</p>
                    {n.body && <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{n.body}</p>}
                    <p className="mt-1 text-[0.65rem] text-muted-foreground/60">
                      {new Date(n.created_at).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex flex-shrink-0 flex-col items-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    {!n.read && (
                      <button
                        onClick={e => { e.stopPropagation(); handleMarkRead(n.id); }}
                        title={t("notifications.markRead")}
                        className="rounded p-0.5 hover:text-primary"
                      >
                        <Check className="h-3 w-3" />
                      </button>
                    )}
                    <button
                      onClick={e => { e.stopPropagation(); handleDelete(n.id, n.read); }}
                      title={t("notifications.delete")}
                      className="rounded p-0.5 hover:text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
