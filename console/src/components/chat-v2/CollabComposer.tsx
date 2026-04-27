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
import { useEffect, useRef, useState } from "react";
import { ImagePlus, Paperclip, RefreshCw, Send, Square, X } from "lucide-react";
import { api } from "@/lib/api";
import * as Y from "yjs";
import getCaretCoordinates from "textarea-caret";
import type { ProjectYjs } from "@/hooks/useProjectYjs";
import type { HydraHiveRuntime } from "./hydrahive-runtime";
import { CoachFeedbackCard } from "./ChatShell";
import { cn } from "@/lib/utils";
import VoiceChatButton from "@/components/VoiceChatButton";

type RemoteCursor = {
  clientId: number;
  name: string;
  hue: string;
  anchor: number;
  head: number;
};

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
  projectId,
}: {
  yjs: ProjectYjs;
  runtime: HydraHiveRuntime;
  disabled?: boolean;
  projectId?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastTextRef = useRef<string>("");
  const [textValue, setTextValue] = useState("");
  const [remoteCursors, setRemoteCursors] = useState<RemoteCursor[]>([]);
  const [textareaVersion, setTextareaVersion] = useState(0);
  const [uploadStatus, setUploadStatus] = useState<"idle" | "uploading" | "error">("idle");

  // #554 H11: eigene Cursor-Position an awareness melden und remote-Cursor
  // einsammeln. Wir triggern bei jedem relevanten Event ein Re-Layout der
  // Overlay-Spans, damit die Position dem Caret folgt.
  useEffect(() => {
    const aw = yjs.awareness;
    const localId = aw.clientID;
    const handler = () => {
      const next: RemoteCursor[] = [];
      aw.getStates().forEach((state, clientId) => {
        if (clientId === localId) return;
        const user = (state?.user ?? {}) as { name?: string; hue?: string };
        const cur = state?.cursor as { anchor?: number; head?: number } | undefined;
        if (!cur || typeof cur.anchor !== "number" || typeof cur.head !== "number") return;
        next.push({
          clientId,
          name: user.name || `user-${clientId}`,
          hue: user.hue || "--candy-cyan",
          anchor: cur.anchor,
          head: cur.head,
        });
      });
      setRemoteCursors(next);
    };
    handler();
    aw.on("change", handler);
    return () => {
      aw.off("change", handler);
    };
  }, [yjs.awareness]);

  // Eigenen Cursor senden wann immer sich selection oder text ändert.
  const publishLocalCursor = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    yjs.awareness.setLocalStateField("cursor", {
      anchor: ta.selectionStart,
      head: ta.selectionEnd,
    });
  };

  /** Text am Cursor einfügen — für Voice-Transcripts + Slash-Chip-Writes. */
  const insertAtCursor = (text: string) => {
    if (!text) return;
    const ta = textareaRef.current;
    const pos = ta ? ta.selectionStart : yjs.ytext.length;
    yjs.ydoc.transact(() => {
      yjs.ytext.insert(pos, text);
    }, "local");
    // observe-Handler wird die Textarea aktualisieren; optionally Cursor
    // hinter den Insert setzen.
    requestAnimationFrame(() => {
      if (!ta) return;
      const next = pos + text.length;
      try { ta.setSelectionRange(next, next); } catch { /* ignore */ }
      ta.focus();
    });
  };

  // Remote → local: Y.Text.observe kippt Änderungen in die Textarea.
  // Wir behalten die Cursor-Position heuristisch bei (falls vor dem Edit war,
  // bleibt sie; falls danach, verschiebt sich mit dem Delta).
  useEffect(() => {
    const applyFromYjs = () => {
      const remote = yjs.ytext.toString();
      if (remote === lastTextRef.current) return;
      const ta = textareaRef.current;
      const prevSelStart = ta?.selectionStart ?? remote.length;
      const prevSelEnd = ta?.selectionEnd ?? remote.length;
      lastTextRef.current = remote;
      setTextValue(remote);
      requestAnimationFrame(() => {
        const nextTa = textareaRef.current;
        if (!nextTa) return;
        try {
          nextTa.setSelectionRange(prevSelStart, prevSelEnd);
        } catch { /* textarea may not yet be focused */ }
      });
      // Remote-Updates können die eigene Caret-Pixel-Position verschieben
      // (Textänderung vor dem Cursor). Overlay neu rendern.
      setTextareaVersion((v) => v + 1);
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
    lastTextRef.current = after;
    setTextValue(after);
    yjs.ydoc.transact(() => {
      if (removed > 0) yjs.ytext.delete(start, removed);
      if (inserted) yjs.ytext.insert(start, inserted);
    }, "local");
    publishLocalCursor();
    setTextareaVersion((v) => v + 1);
  };

  const sendNow = async (skipCoach = false) => {
    const text = yjs.ytext.toString().trim();
    if (!text || runtime.isRunning) return;
    // Optimistic clear: leeren BEVOR wir auf den Stream warten, sonst bleibt
    // der Text bis die Antwort fertig ist im Feld (Till-Bug). Falls Coach
    // die Nachricht abfängt, steht der Originaltext in coachFeedback.content
    // und der User kann per "Trotzdem senden" / "Vorschlag übernehmen"
    // reagieren — brauchen den Y.Text dafür nicht zurückzuspielen.
    yjs.clearText();
    lastTextRef.current = "";
    setTextValue("");
    await runtime.sendText(text, skipCoach);
  };

  const sendAnyway = () => {
    const text = yjs.ytext.toString().trim();
    if (!text) {
      // Fallback auf aktuelles Coach-Feedback-Content wenn Textarea leer ist
      const fb = runtime.coachFeedback?.content;
      if (fb) void runtime.sendText(fb, true).then((ok) => { if (ok) yjs.clearText(); });
      runtime.clearCoachFeedback();
      return;
    }
    runtime.clearCoachFeedback();
    void sendNow(true);
  };

  const useCoachSuggestion = () => {
    const suggestion = runtime.coachFeedback?.suggestion;
    if (!suggestion) return;
    // Y.Text auf Suggestion umsetzen — alle Collab-User sehen das neue.
    yjs.ydoc.transact(() => {
      yjs.ytext.delete(0, yjs.ytext.length);
      yjs.ytext.insert(0, suggestion);
    }, "local");
    runtime.clearCoachFeedback();
    // Cursor ans Ende
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      const len = suggestion.length;
      try { ta.setSelectionRange(len, len); } catch { /* ignore */ }
      ta.focus();
    });
  };

  const useFollowUp = (text: string) => {
    yjs.ydoc.transact(() => {
      yjs.ytext.delete(0, yjs.ytext.length);
      yjs.ytext.insert(0, text);
    }, "local");
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      try { ta.setSelectionRange(text.length, text.length); } catch { /* ignore */ }
      ta.focus();
    });
  };

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      void sendNow(false);
    }
  };

  return (
    <>
      {runtime.coachFeedback ? (
        <div className="mx-auto mb-3 max-w-4xl">
          <CoachFeedbackCard
            reason={runtime.coachFeedback.reason}
            suggestion={runtime.coachFeedback.suggestion}
            onSendAnyway={sendAnyway}
            onUseSuggestion={useCoachSuggestion}
            onDismiss={runtime.clearCoachFeedback}
          />
        </div>
      ) : null}
      {runtime.followUpChips.length > 0 && !runtime.isRunning ? (
        <div className="mx-auto mb-3 flex max-w-4xl flex-wrap gap-2">
          {runtime.followUpChips.map((suggestion, index) => (
            <button
              key={`${suggestion}-${index}`}
              type="button"
              onClick={() => useFollowUp(suggestion)}
              className="chip-candy rounded-full px-3 py-1.5 text-xs font-medium"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}
      <div className="mx-auto mb-2 flex max-w-4xl items-center gap-2">
        <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={runtime.coachEnabled}
            onChange={(event) => runtime.toggleCoach(event.target.checked)}
            className="h-3 w-3 rounded"
          />
          Prompt-Coach
          {runtime.coachChecking ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
        </label>
      </div>
      {runtime.pendingImages.length > 0 ? (
        <div className="mx-auto mb-3 flex max-w-4xl flex-wrap gap-2">
          {runtime.pendingImages.map((image, index) => (
            <div key={`${image.preview}-${index}`} className="group relative">
              <img
                src={image.preview}
                alt=""
                className="h-16 w-16 rounded-xl border border-border object-cover shadow-sm"
              />
              <button
                type="button"
                onClick={() => runtime.removeImage(index)}
                className="absolute -right-2 -top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow transition hover:bg-destructive hover:text-destructive-foreground"
                aria-label="Bild entfernen"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mx-auto flex max-w-4xl items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-lg">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => {
            const files = event.target.files;
            if (files) runtime.addImages(files);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={runtime.isRunning || runtime.pendingImages.length >= 5}
          onClick={() => fileInputRef.current?.click()}
          className="btn-glass inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-muted-foreground disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Bild hochladen"
        >
          <ImagePlus className={cn("h-4 w-4", runtime.pendingImages.length > 0 && "text-primary")} />
        </button>
        <div className="relative flex-1">
          <textarea
            ref={textareaRef}
            value={textValue}
            rows={1}
            disabled={disabled}
            placeholder="Nachricht schreiben..."
            onChange={onInput}
            onKeyDown={onKeyDown}
            onKeyUp={publishLocalCursor}
            onClick={publishLocalCursor}
            onSelect={publishLocalCursor}
            className="max-h-40 min-h-11 w-full resize-none bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
          />
          <RemoteCursorOverlay
            textarea={textareaRef.current}
            cursors={remoteCursors}
            refreshKey={textareaVersion}
          />
        </div>
        <VoiceChatButton
          onTranscript={(text) => insertAtCursor(text)}
          disabled={runtime.isRunning}
          className="!h-10 !w-10 !rounded-xl !border-border !p-0"
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
        {projectId && (
          <label
            className={`btn-glass inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-xl ${uploadStatus === "uploading" ? "opacity-50 pointer-events-none" : ""} ${uploadStatus === "error" ? "text-red-500" : ""}`}
            title={uploadStatus === "error" ? "Upload fehlgeschlagen" : "Datei hochladen"}
          >
            <input
              type="file"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setUploadStatus("uploading");
                try {
                  const result = await api.uploadFile(projectId, file);
                  const sizeKB = (result.size / 1024).toFixed(1);
                  insertIntoYjsComposer(yjs, `[Datei hochgeladen: ${result.path} (${sizeKB} KB)]`);
                  setUploadStatus("idle");
                } catch (err) {
                  console.error('Upload failed:', err);
                  setUploadStatus("error");
                  setTimeout(() => setUploadStatus("idle"), 3000);
                }
                e.target.value = '';
              }}
            />
            {uploadStatus === "uploading"
              ? <RefreshCw className="h-4 w-4 animate-spin" />
              : <Paperclip className={`h-4 w-4 ${uploadStatus === "error" ? "text-red-500" : ""}`} />
            }
          </label>
        )}
        <button
          type="button"
          onClick={() => void sendNow(false)}
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
    </>
  );
}

/** Public-Helper: Text in den shared Composer schreiben (z.B. Slash-Chips).
 * Insert am Ende damit nichts überschrieben wird — Text ist common-editable. */
export function insertIntoYjsComposer(yjs: ProjectYjs, text: string): void {
  if (!text) return;
  yjs.ydoc.transact(() => {
    yjs.ytext.insert(yjs.ytext.length, text);
  }, "local");
}

/** Overlay für Remote-Cursor-Marker über dem Composer-Textarea.
 *
 * getCaretCoordinates (textarea-caret) rendert eine unsichtbare Mirror-div
 * im Hintergrund, um Pixel-Koordinaten für einen gegebenen Zeichen-Offset
 * zu berechnen. Aufruf pro Frame bei Änderung — bei langen Texten evtl.
 * langsam, für Prompt-Zeilen (< 500 Zeichen) unproblematisch.
 *
 * refreshKey erzwingt ein Re-Render bei Textänderung (dann stimmen die
 * Koordinaten wieder mit dem neuen Layout überein).
 */
function RemoteCursorOverlay({
  textarea,
  cursors,
  refreshKey,
}: {
  textarea: HTMLTextAreaElement | null;
  cursors: RemoteCursor[];
  refreshKey: number;
}) {
  void refreshKey;
  if (!textarea || cursors.length === 0) return null;
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {cursors.map((c) => {
        let coords: { top: number; left: number; height: number };
        try {
          coords = getCaretCoordinates(textarea, c.head);
        } catch {
          return null;
        }
        const color = `hsl(var(${c.hue}))`;
        return (
          <span key={c.clientId} style={{ left: coords.left, top: coords.top }} className="absolute">
            <span
              className="inline-block w-[2px] animate-pulse"
              style={{
                backgroundColor: color,
                height: coords.height,
              }}
            />
            <span
              className="absolute -top-4 left-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[9px] font-semibold shadow-sm"
              style={{
                backgroundColor: `hsl(var(${c.hue}) / 0.85)`,
                color: "#fff",
                borderColor: color,
              }}
            >
              {c.name}
            </span>
          </span>
        );
      })}
    </div>
  );
}

export type { Y };
