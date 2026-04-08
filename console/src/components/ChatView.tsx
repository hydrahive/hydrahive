/**
 * ChatView — Shared Chat-Komponente für alle Chat-Pages (#491)
 *
 * Rendert Messages, Tool-Badges, Input-Area, Lightbox, Suggestions.
 * Wird von ChatPage, AgentChatPage und MyAgentPage eingebettet.
 */
import { memo, useEffect } from "react";
import { Bot, User, Terminal, Send, Square, Smile, Plus, RotateCcw, ImagePlus, RefreshCw, X, Loader2, Network } from "lucide-react";
import ReactMarkdown from "react-markdown";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import VoiceChatButton from "@/components/VoiceChatButton";
import OAuthUsageBar from "@/components/OAuthUsageBar";
import type { ChatMessage } from "@/hooks/useChatStream";

// ── MsgTime ─────────────────────────────────────────────────────────────────

const MsgTime = memo(function MsgTime({ iso }: { iso?: string }) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return <span className="text-[10px] text-muted-foreground/50">{d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</span>;
  } catch { return null; }
});

// ── Props ───────────────────────────────────────────────────────────────────

export interface ChatViewProps {
  // From useChatStream hook
  messages: ChatMessage[];
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  sending: boolean;
  error: string;
  setError: (v: string) => void;
  showEmoji: boolean;
  setShowEmoji: React.Dispatch<React.SetStateAction<boolean>>;
  followUpChips: string[];
  setFollowUpChips: (v: string[]) => void;
  lightboxSrc: string | null;
  setLightboxSrc: (v: string | null) => void;
  pendingImages: { data: string; media_type: string; preview: string }[];
  setPendingImages: (fn: (prev: any[]) => any[]) => void;
  activeTool: { name: string; detail: string } | null;
  streamingMsgId: string | null;
  doneMsgId: string | null;
  elapsed: number;
  coachEnabled: boolean;
  toggleCoach: (v: boolean) => void;
  coachFeedback: { ok: boolean; suggestion?: string; reason?: string } | null;
  setCoachFeedback: (v: any) => void;
  coachChecking: boolean;
  // Refs
  bottomRef: React.RefObject<HTMLDivElement>;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  fileInputRef: React.RefObject<HTMLInputElement>;
  // Actions
  send: (contentOverride?: string) => void;
  abort: () => void;
  handleImageUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  // Display options
  showWorkers?: boolean;
  viewSession?: { id: string; messages: ChatMessage[]; startedAt: string } | null;
  /** Show OAuth usage bar (default: true) */
  showOAuthBar?: boolean;
  /** Extra content rendered above the input (e.g. slash command chips) */
  headerSlot?: React.ReactNode;
  /** Custom CSS class for the outer wrapper */
  className?: string;
  // Translations
  t: (key: string, opts?: Record<string, string>) => string;
  // Slash commands for suggestion dropdown
  slashCommands?: { cmd: string; desc: string }[];
}

// ── Component ───────────────────────────────────────────────────────────────

export function ChatView(props: ChatViewProps) {
  const {
    messages, input, setInput, sending, error, setError,
    showEmoji, setShowEmoji, followUpChips, setFollowUpChips,
    lightboxSrc, setLightboxSrc, pendingImages, setPendingImages,
    activeTool, streamingMsgId, doneMsgId, elapsed,
    coachEnabled, toggleCoach, coachFeedback, setCoachFeedback, coachChecking,
    bottomRef, textareaRef, fileInputRef,
    send, abort, handleImageUpload,
    showWorkers, viewSession,
    showOAuthBar = true, headerSlot, className,
    t,
    slashCommands = [],
  } = props;

  const displayMessages = viewSession ? viewSession.messages : messages;

  // Slash command suggestions
  const suggestions = input.startsWith("/")
    ? slashCommands.filter(c => c.cmd.startsWith(input.split(" ")[0]))
    : [];
  const showSuggestDropdown = suggestions.length > 0 && input.length > 0;

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <>
      {showOAuthBar && <OAuthUsageBar />}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-4 space-y-4">
        {displayMessages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center space-y-3 text-center text-muted-foreground">
            <Bot className="h-10 w-10" />
            <p className="text-sm">{t("chat.emptyChat", { defaultValue: "Starte eine Konversation..." })}</p>
          </div>
        )}
        {displayMessages.map((msg) => {
          // Tool messages: group consecutive
          if (msg.role === "tool") {
            const msgIdx = displayMessages.indexOf(msg);
            if (msgIdx > 0 && displayMessages[msgIdx - 1]?.role === "tool") return null;
            const toolGroup: ChatMessage[] = [msg];
            for (let i = msgIdx + 1; i < displayMessages.length && displayMessages[i].role === "tool"; i++) toolGroup.push(displayMessages[i]);
            const badges = toolGroup.filter(tm => !tm.content.startsWith("__IMG__"));
            const images = toolGroup.filter(tm => tm.content.startsWith("__IMG__"));
            return (
              <div key={msg.id} className="flex flex-col items-center gap-2">
                {badges.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 max-w-[85%] justify-center">
                    {badges.map(tm => {
                      const [toolName, ...detailParts] = tm.content.split("|");
                      const detail = detailParts.join("|");
                      return (
                        <span key={tm.id} title={detail || toolName}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary/70 font-mono cursor-default hover:bg-primary/10 transition-colors">
                          <Terminal className="h-2.5 w-2.5" />
                          {toolName}
                        </span>
                      );
                    })}
                  </div>
                )}
                {images.map(tm => {
                  const [label, ...srcParts] = tm.content.replace("__IMG__", "").split("|");
                  const src = srcParts.join("|");
                  return (
                    <div key={tm.id} className="max-w-[75%] rounded-lg border bg-card p-2">
                      <img src={src} alt={label} className="rounded-md max-h-[400px] w-auto cursor-pointer hover:opacity-80 transition-opacity" onClick={() => setLightboxSrc(src)} />
                      <div className="text-[10px] text-muted-foreground mt-1 text-center font-mono">{label}</div>
                    </div>
                  );
                })}
              </div>
            );
          }
          // System messages
          if (msg.role === "system") {
            return (
              <div key={msg.id} className="flex justify-center">
                <div className="flex max-w-[85%] items-start gap-2 rounded-2xl border border-border/50 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                  <Terminal className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary/60" />
                  <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-0.5">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                </div>
              </div>
            );
          }
          // User / Assistant
          return (
            <div key={msg.id} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
              <div className={`mb-0.5 px-12 ${msg.role === "user" ? "text-right" : "text-left"}`}><MsgTime iso={msg.ts} /></div>
              <div className={`flex gap-3 w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "assistant" && (
                  <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                )}
                <div className="flex max-w-[78%] flex-col gap-1">
                  <div className={`break-words rounded-2xl px-4 py-3 text-sm ${msg.role === "user" ? "bg-primary text-primary-foreground shadow-sm" : "border bg-card prose prose-sm max-w-none dark:prose-invert"}`}>
                    {msg.role === "user" ? (
                      <>
                        {msg._images && msg._images.length > 0 && (
                          <div className="flex gap-1 mb-1 flex-wrap">
                            {msg._images.map((src, i) => (
                              <img key={i} src={src} alt="" className="h-20 rounded-md" />
                            ))}
                          </div>
                        )}
                        <span className="whitespace-pre-wrap">{msg.content}</span>
                      </>
                    ) : streamingMsgId === msg.id && !msg.content ? (
                      <div className="flex h-5 items-center gap-1">
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                      </div>
                    ) : (
                      <>
                        <ReactMarkdown components={{ img: ({ src, alt }) => src?.startsWith("data:image") ? (
                          <img src={src} alt={alt || ""} className="rounded-md max-h-[400px] w-auto cursor-pointer hover:opacity-80 transition-opacity my-2" onClick={() => setLightboxSrc(src)} />
                        ) : <img src={src} alt={alt || ""} /> }}>{msg.content}</ReactMarkdown>
                        {streamingMsgId === msg.id && activeTool && (activeTool.name === "ask_agent" || activeTool.name === "dispatch_task") ? (
                          <div className="mt-2 flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary">
                            <Loader2 className="h-3.5 w-3.5 animate-spin flex-shrink-0" />
                            <span className="font-medium">{activeTool.detail}</span>
                            {elapsed > 0 && <span className="ml-auto text-muted-foreground">{elapsed}s</span>}
                          </div>
                        ) : streamingMsgId === msg.id ? (
                          <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-sm bg-primary/70 align-text-bottom" />
                        ) : doneMsgId === msg.id ? (
                          <span className="ml-1 inline-block text-xs text-green-500 align-text-bottom">✓</span>
                        ) : null}
                      </>
                    )}
                  </div>
                  {/* Worker badges (ChatPage only) */}
                  {showWorkers && msg.role === "assistant" && msg.workers && msg.workers.length > 0 && (
                    <div className="flex flex-wrap gap-1 px-1">
                      {msg.workers.map(w => (
                        <span key={w} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground"><Network className="h-2.5 w-2.5" />{w}</span>
                      ))}
                    </div>
                  )}
                  {/* Token usage + fallback */}
                  {msg.role === "assistant" && (msg.tokenUsage || msg.isFallback) && (
                    <div className="flex gap-1 px-1 flex-wrap">
                      {msg.isFallback && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-orange-500/10 border border-orange-500/30 px-2 py-0.5 text-xs text-orange-500">
                          Fallback: {msg.model}
                        </span>
                      )}
                      {msg.tokenUsage && (msg.tokenUsage.input > 0 || msg.tokenUsage.output > 0) && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                          ↑ {msg.tokenUsage.input.toLocaleString()} ↓ {msg.tokenUsage.output.toLocaleString()} Tokens
                          {msg.tokenUsage.rounds && msg.tokenUsage.rounds > 1 && <span className="opacity-60">· {msg.tokenUsage.rounds} Runden</span>}
                          {(msg.tokenUsage.cache_read ?? 0) > 0 && <span className="text-green-500">· {msg.tokenUsage.cache_read!.toLocaleString()} cached</span>}
                          {(msg.tokenUsage.cache_write ?? 0) > 0 && <span className="text-blue-400">· {msg.tokenUsage.cache_write!.toLocaleString()} cache-write</span>}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                {msg.role === "user" && (
                  <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-secondary">
                    <User className="h-4 w-4" />
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {/* Loading indicator when no assistant message yet */}
        {sending && displayMessages[displayMessages.length - 1]?.role !== "assistant" && (
          <div className="flex justify-start gap-3">
            <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-2xl bg-primary/10">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div className="rounded-2xl border bg-card px-4 py-3">
              <div className="flex h-5 items-center gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Error */}
      {error && (
        <div className="mx-4 mb-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive flex items-center gap-2">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError("")} className="p-1 hover:bg-destructive/10 rounded"><X className="h-3.5 w-3.5" /></button>
        </div>
      )}

      {/* Input Area */}
      <div className="border-t px-4 py-3 flex-shrink-0">
        {/* Coach feedback */}
        {coachFeedback && !coachFeedback.ok && (
          <div className="mb-2 rounded-xl border border-orange-500/30 bg-orange-500/5 px-3 py-2 text-xs">
            <p className="font-medium text-orange-500">{coachFeedback.reason}</p>
            {coachFeedback.suggestion && <p className="text-muted-foreground mt-1">{coachFeedback.suggestion}</p>}
            <div className="flex gap-2 mt-2">
              <button onClick={() => { setCoachFeedback(null); send(input); }} className="text-xs text-primary hover:underline">{t("chat.sendAnyway", { defaultValue: "Trotzdem senden" })}</button>
              <button onClick={() => setCoachFeedback(null)} className="text-xs text-muted-foreground hover:underline">{t("common.cancel", { defaultValue: "Abbrechen" })}</button>
            </div>
          </div>
        )}
        {/* Page-specific header slot (e.g. slash command chips) */}
        {headerSlot}
        {/* Coach toggle */}
        <div className="flex items-center gap-2 mb-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
            <input type="checkbox" checked={coachEnabled} onChange={e => toggleCoach(e.target.checked)} className="h-3 w-3 rounded" />
            Prompt-Coach {coachChecking && <RefreshCw className="h-3 w-3 animate-spin" />}
          </label>
        </div>
        {/* Follow-up chips */}
        {followUpChips.length > 0 && !sending && (
          <div className="flex gap-1.5 mb-2 flex-wrap">
            {followUpChips.map((s, i) => (
              <button key={i} onClick={() => { setFollowUpChips([]); setInput(s); setTimeout(() => textareaRef.current?.focus(), 50); }}
                className="px-3 py-1 text-xs rounded-full border border-primary/30 bg-primary/5 text-primary hover:bg-primary/15 transition-colors">
                {s}
              </button>
            ))}
          </div>
        )}
        {/* Image previews */}
        {pendingImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pendingImages.map((img, i) => (
              <div key={i} className="relative group">
                <img src={img.preview} alt="" className="h-16 w-16 object-cover rounded-lg border" />
                <button onClick={() => setPendingImages(prev => prev.filter((_, j) => j !== i))}
                  className="absolute -top-1 -right-1 bg-destructive text-white rounded-full w-4 h-4 text-[10px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                  X
                </button>
              </div>
            ))}
          </div>
        )}
        {/* Textarea + buttons */}
        <div className="flex gap-2 items-end">
          <textarea ref={textareaRef} value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t("chat.messagePlaceholder", { defaultValue: "Nachricht schreiben..." })}
            rows={1}
            disabled={!!viewSession}
            className="flex-1 min-w-0 px-3 py-2 text-sm border rounded-xl bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            style={{ maxHeight: "120px", overflowY: "auto" }} />
          <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleImageUpload} />
          <button onClick={() => setShowEmoji(e => !e)} className="hidden sm:flex p-2 rounded-xl hover:bg-accent text-muted-foreground" type="button">
            <Smile className="h-4 w-4" />
          </button>
          <button onClick={() => fileInputRef.current?.click()} className="p-2 rounded-xl hover:bg-accent text-muted-foreground" type="button">
            <ImagePlus className={`h-4 w-4 ${pendingImages.length > 0 ? "text-primary" : ""}`} />
          </button>
          <VoiceChatButton onTranscript={(t) => setInput(prev => prev ? prev + " " + t : t)} disabled={sending || !!viewSession} className="!p-2 !rounded-xl" />
          {sending ? (
            <button onClick={abort} className="p-2 rounded-xl bg-destructive text-destructive-foreground hover:bg-destructive/90" type="button">
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button onClick={() => send()} disabled={!input.trim() && pendingImages.length === 0}
              className="p-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50" type="button">
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
        {/* Emoji picker */}
        {showEmoji && (
          <div className="absolute bottom-16 right-4 z-50">
            <EmojiPicker theme={Theme.AUTO} onEmojiClick={(d: EmojiClickData) => { setInput(v => v + d.emoji); setShowEmoji(false); }} />
          </div>
        )}
        {/* Slash command suggestions */}
        {showSuggestDropdown && (
          <div className="absolute bottom-full mb-1 left-4 right-4 bg-popover border rounded-xl shadow-lg p-1 z-50">
            {suggestions.map((s, i) => (
              <button key={s.cmd} onClick={() => { setInput(s.cmd + " "); }}
                className="w-full text-left px-3 py-1.5 rounded-lg text-sm hover:bg-accent flex justify-between">
                <span className="font-mono text-primary">{s.cmd}</span>
                <span className="text-muted-foreground text-xs">{s.desc}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Lightbox */}
      {lightboxSrc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 cursor-pointer" onClick={() => setLightboxSrc(null)}>
          <img src={lightboxSrc} alt="" className="max-h-[90vh] max-w-[90vw] rounded-lg" />
        </div>
      )}
    </>
  );
}
