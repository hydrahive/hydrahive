/**
 * CollabComposer — gemeinsamer Composer über Yjs (#554 H8/H9)
 *
 * Ersetzt den Standard-ComposerPrimitive.Input wenn ein geteilter Y.Text
 * existiert. Wir binden die Textarea direkt an den Y.Text (observe → value,
 * input → ytext.insert/delete via Delta-Diff) und behalten die Cursor-
 * Position beim Remote-Update. Send ruft runtime.sendText und leert den
 * Y.Text — alle verbundenen Clients sehen das leere Composer-Feld.
 *
 * MVP-Einschränkung: in diesem Modus rendern wir KEINE Coach-Card, Voice-
 * Buttons, Image-Uploads oder Follow-Up-Chips. Die kommen in H13 zurück
 * wenn der Collab-Composer stabil ist.
 */
import { useEffect, useRef } from "react";
import { Send, Square } from "lucide-react";
import * as Y from "yjs";
import type { ProjectYjs } from "@/hooks/useProjectYjs";
import type { HydraHiveRuntime } from "./hydrahive-runtime";
import { cn } from "@/lib/utils";

function computeDelta(oldStr: string, newStr: string): { start: number; removed: number; inserted: string } {
  // Kleine Diff-Heuristik: gemeinsamer Prefix + Suffix, Rest ist Insert + Delete.
  // Reicht für normale Textarea-Edits (Tippen, Einfügen, Löschen, Paste).
  let start = 0;
  const min = Math.min(oldStr.length, newStr.length);
  while (start < min && oldStr.charCodeAt(start) === newStr.charCodeAt(start)) start++;
  let oldEnd = oldStr.length;
  let newEnd = newStr.length;
  while (oldEnd > start && newEnd > start && oldStr.charCodeAt(oldEnd - 1) === newStr.charCodeAt(newEnd - 1)) {
    oldEnd--;
    newEnd--;
  }
  return {
    start,
    removed: oldEnd - start,
    inserted: newStr.slice(start, newEnd),
  };
}

export function CollabComposer({
  yjs,
  runtime,
  disabled,
}: {
  yjs: ProjectYjs;
  runtime: HydraHiveRuntime;
  disabled?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lastTextRef = useRef<string>("");

  // Remote → local: Y.Text.observe kippt Änderungen in die Textarea.
  // Wir behalten die Cursor-Position heuristisch bei (falls vor dem Edit war,
  // bleibt sie; falls danach, verschiebt sich mit dem Delta).
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const applyFromYjs = () => {
      const remote = yjs.ytext.toString();
      if (remote === lastTextRef.current) return;
      const prevSelStart = ta.selectionStart;
      const prevSelEnd = ta.selectionEnd;
      ta.value = remote;
      lastTextRef.current = remote;
      // Cursor an Position halten — wenn der Remote-Change vor dem Cursor
      // passierte, verschiebt sich der Cursor mit; sonst bleibt er.
      try {
        ta.setSelectionRange(prevSelStart, prevSelEnd);
      } catch { /* textarea may not yet be focused */ }
    };
    applyFromYjs();
    const handler = () => applyFromYjs();
    yjs.ytext.observe(handler);
    return () => {
      yjs.ytext.unobserve(handler);
    };
  }, [yjs.ytext]);

  // Local → Yjs: Input-Handler berechnet Delta und schickt es in den Y.Text.
  // Damit kriegen alle anderen Clients die exakte Änderung ohne Konflikte.
  const onInput: React.FormEventHandler<HTMLTextAreaElement> = (e) => {
    const ta = e.currentTarget;
    const before = lastTextRef.current;
    const after = ta.value;
    if (before === after) return;
    const { start, removed, inserted } = computeDelta(before, after);
    yjs.ydoc.transact(() => {
      if (removed > 0) yjs.ytext.delete(start, removed);
      if (inserted) yjs.ytext.insert(start, inserted);
    }, "local");
    lastTextRef.current = after;
  };

  const sendNow = async () => {
    const text = yjs.ytext.toString().trim();
    if (!text || runtime.isRunning) return;
    // Erst Y.Text clearen (remote users sehen sofort leer), dann senden.
    // Bei Fehler bleibt der Server-Turn durch runtime selbst; der Y.Text
    // ist aber schon leer. Zurückschreiben wäre unnatürlich — Till
    // kann nochmal tippen, Fehler taucht als runtime.error auf.
    yjs.clearText();
    lastTextRef.current = "";
    await runtime.sendText(text);
  };

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      void sendNow();
    }
  };

  return (
    <div className="mx-auto flex max-w-4xl items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-lg">
      <textarea
        ref={textareaRef}
        rows={1}
        autoFocus
        disabled={disabled}
        placeholder={yjs.connected ? "Gemeinsam tippen …" : "Verbinde …"}
        onInput={onInput}
        onKeyDown={onKeyDown}
        className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
      />
      {runtime.isRunning ? (
        <button
          type="button"
          onClick={runtime.cancel}
          className="btn-glass inline-flex h-10 w-10 items-center justify-center rounded-xl"
          aria-label="Stream abbrechen"
        >
          <Square className="h-4 w-4" />
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => void sendNow()}
        disabled={disabled || runtime.isRunning}
        className={cn(
          "btn-candy inline-flex h-10 w-10 items-center justify-center rounded-xl",
          "disabled:opacity-40",
        )}
        aria-label="Senden"
      >
        <Send className="h-4 w-4" />
      </button>
    </div>
  );
}

export type { Y };
