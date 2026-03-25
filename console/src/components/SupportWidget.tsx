import { useEffect, useRef, useState } from "react";
import { X, Send, Bot, User, HelpCircle, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { api } from "@/lib/api";

const AGENT_ID = "hydrahive_support";

interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
}

let counter = 0;
function mkId() { return `sw-${++counter}`; }

export function SupportWidget() {
  const [open, setOpen]       = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput]     = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) {
      setTimeout(() => textareaRef.current?.focus(), 50);
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const content = input.trim();
    if (!content || sending) return;
    setInput("");

    const userMsg: Msg = { id: mkId(), role: "user", content };
    const asstMsg: Msg = { id: mkId(), role: "assistant", content: "" };
    setMessages(ms => [...ms, userMsg, asstMsg]);
    setSending(true);

    try {
      const token = localStorage.getItem("hydrahive_token") ?? "";
      const res = await fetch(`/api/agents/${AGENT_ID}/message/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({ content }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.text !== undefined) {
                setMessages(ms => ms.map(m =>
                  m.id === asstMsg.id ? { ...m, content: m.content + evt.text } : m
                ));
              } else if (evt.done) {
                break outer;
              } else if (evt.error) {
                throw new Error(evt.error);
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      setMessages(ms => ms.map(m =>
        m.id === asstMsg.id ? { ...m, content: "Fehler: " + (e instanceof Error ? e.message : "Unbekannter Fehler") } : m
      ));
    } finally {
      setSending(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => setOpen(o => !o)}
        className="fixed bottom-5 right-5 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition hover:bg-primary/90 hover:scale-105"
        title="HydraHive Support"
      >
        {open ? <X className="h-5 w-5" /> : <HelpCircle className="h-5 w-5" />}
      </button>

      {/* Chat window */}
      {open && (
        <div className="fixed bottom-20 right-5 z-50 flex w-80 flex-col rounded-2xl border bg-card shadow-2xl"
          style={{ height: "420px" }}>

          {/* Header */}
          <div className="flex items-center gap-2.5 rounded-t-2xl border-b bg-primary/5 px-4 py-3">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10">
              <Bot className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold leading-none">HydraHive Support</p>
              <p className="mt-0.5 text-xs text-muted-foreground">Doku · Konfiguration · Prompts</p>
            </div>
            <button onClick={() => setOpen(false)} className="rounded-lg p-1 hover:bg-muted transition-colors">
              <X className="h-4 w-4 text-muted-foreground" />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
            {messages.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted-foreground">
                <HelpCircle className="h-8 w-8 opacity-30" />
                <p className="text-xs">Frag mich alles zu HydraHive —<br />Bedienung, Konfiguration, Prompts.</p>
              </div>
            )}
            {messages.map(msg => (
              <div key={msg.id} className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "assistant" && (
                  <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-3 w-3 text-primary" />
                  </div>
                )}
                <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-xs ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "border bg-muted/40 prose prose-xs max-w-none dark:prose-invert"
                }`}>
                  {msg.role === "user"
                    ? <span className="whitespace-pre-wrap">{msg.content}</span>
                    : msg.content
                      ? <ReactMarkdown>{msg.content}</ReactMarkdown>
                      : <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                  }
                </div>
                {msg.role === "user" && (
                  <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-secondary">
                    <User className="h-3 w-3" />
                  </div>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="border-t px-3 py-2.5">
            <div className="flex items-end gap-2">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Frage stellen…"
                rows={1}
                disabled={sending}
                className="flex-1 resize-none rounded-xl border bg-background px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
                style={{ maxHeight: "80px", overflowY: "auto" }}
              />
              <button
                onClick={send}
                disabled={!input.trim() || sending}
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40"
              >
                {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
