/**
 * Notification Queue with Folding + Priority (#479)
 *
 * - Folds identical notifications by key: "Permission denied (x3)"
 * - Priority levels: low, medium, high, immediate
 * - Queue: shows 1 notification at a time, queues the rest
 * - Immediate priority bypasses queue and shows instantly
 * - Invalidation: a notification can replace another by key
 */

import { useCallback, useSyncExternalStore } from "react";

export type NotificationPriority = "low" | "medium" | "high" | "immediate";

export interface NotificationOptions {
  priority?: NotificationPriority;
  timeout?: number;      // ms, default 8000
  icon?: string;         // emoji or short string
}

export interface QueuedNotification {
  id: string;
  key: string;
  message: string;
  priority: NotificationPriority;
  timeout: number;
  icon?: string;
  count: number;         // fold count
  createdAt: number;
}

// ── Priority ordering (higher = more urgent) ──
const PRIORITY_ORDER: Record<NotificationPriority, number> = {
  low: 0,
  medium: 1,
  high: 2,
  immediate: 3,
};

// ── Singleton store ──
let queue: QueuedNotification[] = [];
let active: QueuedNotification | null = null;
let dismissTimer: ReturnType<typeof setTimeout> | null = null;
let idCounter = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

function scheduleNext() {
  if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }

  if (queue.length === 0) {
    active = null;
    emit();
    return;
  }

  // Sort by priority desc, then by createdAt asc
  queue.sort((a, b) => {
    const pd = PRIORITY_ORDER[b.priority] - PRIORITY_ORDER[a.priority];
    if (pd !== 0) return pd;
    return a.createdAt - b.createdAt;
  });

  active = queue.shift()!;
  emit();

  dismissTimer = setTimeout(() => {
    active = null;
    dismissTimer = null;
    scheduleNext();
  }, active.timeout);
}

/**
 * Push a notification into the queue.
 * Same key = fold (increment count) instead of adding a new entry.
 * Immediate priority interrupts current notification.
 */
export function notify(
  key: string,
  message: string,
  options?: NotificationOptions,
): void {
  const priority = options?.priority ?? "medium";
  const timeout = options?.timeout ?? 8000;
  const icon = options?.icon;

  // Check if active notification has the same key → fold into it
  if (active && active.key === key) {
    active = { ...active, count: active.count + 1, message };
    // Reset timer
    if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
    dismissTimer = setTimeout(() => {
      active = null;
      dismissTimer = null;
      scheduleNext();
    }, active.timeout);
    emit();
    return;
  }

  // Check if queued notification has the same key → fold
  const existing = queue.find(n => n.key === key);
  if (existing) {
    existing.count += 1;
    existing.message = message;
    // Upgrade priority if higher
    if (PRIORITY_ORDER[priority] > PRIORITY_ORDER[existing.priority]) {
      existing.priority = priority;
    }
    emit();
    // If it was upgraded to immediate, pull it out and show now
    if (priority === "immediate") {
      queue = queue.filter(n => n.key !== key);
      interruptWith(existing);
    }
    return;
  }

  // New notification
  const notif: QueuedNotification = {
    id: `notif-${++idCounter}`,
    key,
    message,
    priority,
    timeout,
    icon,
    count: 1,
    createdAt: Date.now(),
  };

  if (priority === "immediate") {
    interruptWith(notif);
    return;
  }

  queue.push(notif);

  // If nothing active, show next
  if (!active) {
    scheduleNext();
  } else {
    emit(); // queue changed
  }
}

/** Interrupt current notification and show this one immediately */
function interruptWith(notif: QueuedNotification) {
  if (active) {
    // Put current back into queue so it shows again later
    queue.unshift(active);
  }
  if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }

  active = notif;
  emit();

  dismissTimer = setTimeout(() => {
    active = null;
    dismissTimer = null;
    scheduleNext();
  }, notif.timeout);
}

/**
 * Invalidate/remove notification by key.
 * Removes from queue and from active if matching.
 */
export function invalidate(key: string): void {
  queue = queue.filter(n => n.key !== key);
  if (active && active.key === key) {
    if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
    active = null;
    scheduleNext();
    return;
  }
  emit();
}

/** Dismiss the currently active notification */
export function dismissActive(): void {
  if (!active) return;
  if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
  active = null;
  scheduleNext();
}

// ── React hook ──

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

function getSnapshot(): { active: QueuedNotification | null; queueLength: number } {
  return { active, queueLength: queue.length };
}

// Stable reference cache to avoid unnecessary re-renders
let cachedSnapshot = getSnapshot();
function getSnapshotStable() {
  const next = getSnapshot();
  if (next.active === cachedSnapshot.active && next.queueLength === cachedSnapshot.queueLength) {
    return cachedSnapshot;
  }
  cachedSnapshot = next;
  return cachedSnapshot;
}

export function useNotifications() {
  const snapshot = useSyncExternalStore(subscribe, getSnapshotStable);
  const dismiss = useCallback(() => dismissActive(), []);
  return { ...snapshot, dismiss };
}
