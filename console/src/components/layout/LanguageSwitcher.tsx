/**
 * LanguageSwitcher.tsx — #691
 *
 * Dropdown mit Flagge + Sprachname. Klappt nach oben auf (Button sitzt
 * in der unteren Sidebar-Leiste). Für beliebig viele Sprachen ausgelegt:
 * neue Sprache = eine Zeile in LANGUAGES + ein Locale-File (+ resources
 * in lib/i18n.ts).
 *
 * Keyboard:
 *   Enter/Space auf Button: öffnen
 *   Arrow Up/Down im Menü:  navigieren
 *   Enter im Menü:          wählen
 *   ESC:                    schließen
 *   Click outside:          schließen
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { ChevronUp } from "lucide-react";
import i18n from "@/lib/i18n";

export interface LanguageEntry {
  code: string;
  flag: string;
  name: string;   // Eigenname der Sprache (Deutsch, English, 中文, Español, ...)
}

/** Single Source of Truth — neue Sprachen hier eintragen. */
export const LANGUAGES: LanguageEntry[] = [
  { code: "de", flag: "🇩🇪", name: "Deutsch" },
  { code: "en", flag: "🇬🇧", name: "English" },
  // Tier 2 folgt (#681): 中文 · Español · Français
];

export function LanguageSwitcher() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const current =
    LANGUAGES.find((l) => l.code === i18n.language) ??
    LANGUAGES.find((l) => i18n.language?.startsWith(l.code)) ??
    LANGUAGES[0];

  // Click-outside schließt
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // ESC + Pfeiltasten
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const idx = itemRefs.current.findIndex((r) => r === document.activeElement);
        let next: number;
        if (e.key === "ArrowDown") {
          next = idx < 0 ? 0 : Math.min(idx + 1, LANGUAGES.length - 1);
        } else {
          next = idx < 0 ? LANGUAGES.length - 1 : Math.max(idx - 1, 0);
        }
        itemRefs.current[next]?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Beim Öffnen Fokus aufs aktive Item — sofort keyboard-navigierbar
  useEffect(() => {
    if (!open) return;
    const activeIdx = LANGUAGES.findIndex((l) => l.code === current.code);
    const id = window.setTimeout(() => itemRefs.current[activeIdx]?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open, current.code]);

  const choose = useCallback((code: string) => {
    i18n.changeLanguage(code);
    setOpen(false);
    buttonRef.current?.focus();
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("layout.languageMenuLabel", { defaultValue: "Sprache wählen" })}
        className="flex w-full items-center justify-center gap-1.5 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/10"
      >
        <span className="text-base leading-none" aria-hidden="true">{current.flag}</span>
        <span>{current.code.toUpperCase()}</span>
        <ChevronUp
          className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label={t("layout.languageMenuLabel", { defaultValue: "Sprache wählen" })}
          className="absolute bottom-full left-0 right-0 mb-2 overflow-hidden rounded-2xl border border-white/10 bg-[hsl(var(--sidebar-background))] shadow-lg z-50"
        >
          {LANGUAGES.map((lang, i) => {
            const isActive = lang.code === current.code;
            return (
              <button
                key={lang.code}
                ref={(el) => { itemRefs.current[i] = el; }}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                onClick={() => choose(lang.code)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm text-left text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/10 focus:bg-white/10 focus:outline-none ${isActive ? "bg-white/5 font-medium" : ""}`}
              >
                <span className="text-base leading-none" aria-hidden="true">{lang.flag}</span>
                <span>{lang.name}</span>
                {isActive && (
                  <span className="ml-auto text-xs text-[hsl(var(--sidebar-muted))]" aria-hidden="true">✓</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
