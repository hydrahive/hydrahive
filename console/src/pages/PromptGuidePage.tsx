import { useState } from "react";
import { Lightbulb, Copy, CheckCircle, ChevronDown, ChevronRight, Zap, Target, Layers, AlertTriangle, BookOpen, Settings2, Brain } from "lucide-react";
import { useTranslation } from "react-i18next";

/* ── Collapsible Section ──────────────────────────────────────── */

function Section({ title, icon: Icon, children, defaultOpen }: {
  title: string; icon: React.ElementType; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="rounded-xl border bg-card overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-3 px-5 py-4 text-left hover:bg-muted/30 transition-colors">
        <Icon className="h-5 w-5 text-primary shrink-0" />
        <span className="font-medium flex-1">{title}</span>
        <Chevron className="h-4 w-4 text-muted-foreground" />
      </button>
      {open && <div className="px-5 pb-5 border-t border-border/50">{children}</div>}
    </div>
  );
}

/* ── Example Block ────────────────────────────────────────────── */

function Example({ bad, good }: { bad: string; good: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(good);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-2 my-3">
      <div className="flex items-start gap-2 rounded-lg bg-red-500/5 border border-red-500/20 p-3">
        <AlertTriangle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
        <p className="text-sm text-red-400">{bad}</p>
      </div>
      <div className="flex items-start gap-2 rounded-lg bg-green-500/5 border border-green-500/20 p-3">
        <CheckCircle className="h-4 w-4 text-green-500 shrink-0 mt-0.5" />
        <p className="text-sm text-green-400 flex-1">{good}</p>
        <button onClick={copy} className="text-muted-foreground hover:text-foreground shrink-0" title={t("common.copy") || "Copy"}>
          {copied ? <CheckCircle className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
    </div>
  );
}

/* ── Template Button ──────────────────────────────────────────── */

function Template({ label, prompt }: { label: string; prompt: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(prompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button onClick={copy}
      className="flex items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-sm text-left hover:bg-muted/50 transition-colors w-full">
      {copied ? <CheckCircle className="h-4 w-4 text-green-500 shrink-0" /> : <Copy className="h-4 w-4 text-muted-foreground shrink-0" />}
      <div className="min-w-0">
        <p className="font-medium truncate">{label}</p>
        <p className="text-xs text-muted-foreground truncate">{prompt}</p>
      </div>
    </button>
  );
}

/* ── Main Page ────────────────────────────────────────────────── */

export function PromptGuidePage() {
  const { i18n } = useTranslation();
  const de = i18n.language?.startsWith("de");

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-4">
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
            <Lightbulb className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Prompt in a Nutshell</h1>
            <p className="text-sm text-muted-foreground">
              {de ? "So holst du das Beste aus deinem KI-Agenten heraus." : "How to get the best results from your AI agent."}
            </p>
          </div>
        </div>
      </div>

      {/* ── Progress Tracker ── */}
      <div className="rounded-xl border bg-card px-5 py-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
          {de ? "Module" : "Modules"}
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            { label: de ? "Grundlagen" : "Basics", icon: Zap },
            { label: de ? "Techniken" : "Techniques", icon: Target },
            { label: "HydraHive", icon: Settings2 },
            { label: de ? "Profi" : "Pro", icon: Brain },
          ].map(({ label, icon: Icon }) => (
            <div key={label} className="flex items-center gap-2 rounded-lg bg-muted/30 px-3 py-2 text-xs">
              <Icon className="h-3.5 w-3.5 text-primary shrink-0" />
              <span className="font-medium">{label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Goldene Regeln ── */}
      <Section title={de ? "Die 5 goldenen Regeln" : "The 5 Golden Rules"} icon={Zap} defaultOpen>
        <div className="space-y-4 mt-3">
          <Rule n={1}
            title={de ? "Kontext geben" : "Provide Context"}
            desc={de
              ? "Sag der KI was sie wissen muss. Welches Projekt? Welche Datei? Welche Sprache? Je mehr Kontext, desto bessere Antworten."
              : "Tell the AI what it needs to know. Which project? Which file? Which language? More context = better answers."}
          />
          <Rule n={2}
            title={de ? "Schritt für Schritt" : "Step by Step"}
            desc={de
              ? "Große Aufgaben in kleine Schritte aufteilen. Erst analysieren, dann planen, dann umsetzen. Nicht alles auf einmal."
              : "Break big tasks into small steps. First analyze, then plan, then implement. Not everything at once."}
          />
          <Rule n={3}
            title={de ? "Spezifisch sein" : "Be Specific"}
            desc={de
              ? "\"Schau dir das an\" ist schlecht. \"Lies die Datei server.py und liste alle API-Endpoints auf\" ist gut."
              : "\"Look at this\" is bad. \"Read the file server.py and list all API endpoints\" is good."}
          />
          <Rule n={4}
            title={de ? "Format vorgeben" : "Specify the Format"}
            desc={de
              ? "Sag wie die Antwort aussehen soll: Als Tabelle, als Code, als Zusammenfassung, als Checkliste."
              : "Say how the answer should look: As a table, as code, as a summary, as a checklist."}
          />
          <Rule n={5}
            title={de ? "Iterativ arbeiten" : "Work Iteratively"}
            desc={de
              ? "Erst Ergebnis prüfen, dann verfeinern. \"Das ist gut, aber ändere noch X\" ist besser als alles nochmal von vorne."
              : "Check the result first, then refine. \"That's good, but also change X\" is better than starting over."}
          />
        </div>
      </Section>

      {/* ── Vorher / Nachher ── */}
      <Section title={de ? "Vorher / Nachher Beispiele" : "Before / After Examples"} icon={Target}>
        <div className="space-y-5 mt-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              {de ? "Code analysieren" : "Code Analysis"}
            </p>
            <Example
              bad={de
                ? "Analysiere das System und baue es ein."
                : "Analyze the system and integrate it."}
              good={de
                ? "Lies die Datei guild_system.py und erstelle eine Zusammenfassung: welche Klassen gibt es, welche Abhängigkeiten, welche Datenbank-Tabellen werden genutzt. Danach besprechen wir die Integration Schritt für Schritt."
                : "Read the file guild_system.py and create a summary: what classes exist, what dependencies, what database tables are used. Then we'll discuss the integration step by step."}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              {de ? "Bug fixen" : "Bug Fix"}
            </p>
            <Example
              bad={de ? "Fix den Bug." : "Fix the bug."}
              good={de
                ? "In server.py Zeile 42 kommt ein TypeError wenn ich /api/users aufrufe. Hier ist der Traceback: [Traceback einfügen]. Das erwartete Verhalten ist eine JSON-Liste aller User."
                : "In server.py line 42, there's a TypeError when I call /api/users. Here's the traceback: [paste traceback]. Expected behavior is a JSON list of all users."}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              {de ? "Feature bauen" : "Build a Feature"}
            </p>
            <Example
              bad={de ? "Mach die Webseite schöner." : "Make the website prettier."}
              good={de
                ? "Ändere die Startseite: Header soll blau sein (#2563eb), Logo links, Navigation rechts. Auf Mobilgeräten: Hamburger-Menü. Bitte nur CSS/HTML ändern, kein JavaScript."
                : "Change the homepage: header should be blue (#2563eb), logo on the left, navigation on the right. On mobile: hamburger menu. Please only change CSS/HTML, no JavaScript."}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              {de ? "Daten verarbeiten" : "Data Processing"}
            </p>
            <Example
              bad={de ? "Sortier die Daten." : "Sort the data."}
              good={de
                ? "Lies die CSV-Datei kunden.csv und erstelle eine Tabelle sortiert nach Umsatz (absteigend). Zeige nur Kunden mit Umsatz über 10.000€. Format: Markdown-Tabelle."
                : "Read the CSV file customers.csv and create a table sorted by revenue (descending). Show only customers with revenue over $10,000. Format: Markdown table."}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              {de ? "Recherche" : "Research"}
            </p>
            <Example
              bad={de ? "Was ist besser?" : "What's better?"}
              good={de
                ? "Vergleiche React und Vue.js für eine mittelgroße Business-App mit 10 Entwicklern. Kriterien: Lernkurve, Performance, Ökosystem, TypeScript-Support. Antwort als Vergleichstabelle."
                : "Compare React and Vue.js for a mid-size business app with 10 developers. Criteria: learning curve, performance, ecosystem, TypeScript support. Answer as a comparison table."}
            />
          </div>
        </div>
      </Section>

      {/* ── Fortgeschrittene Tipps ── */}
      <Section title={de ? "Fortgeschrittene Tipps" : "Advanced Tips"} icon={Layers}>
        <div className="space-y-3 mt-3 text-sm">
          <Tip emoji="🎭" title={de ? "Rolle zuweisen" : "Assign a Role"}
            desc={de
              ? "\"Du bist ein Python-Experte mit 10 Jahren Django-Erfahrung\" — die KI passt ihren Stil und Detailgrad an."
              : "\"You are a Python expert with 10 years of Django experience\" — the AI adapts its style and detail level."}
          />
          <Tip emoji="🚧" title={de ? "Grenzen setzen" : "Set Boundaries"}
            desc={de
              ? "\"Ändere nur die Funktion calculate_total, nicht den Rest der Datei\" — verhindert ungewollte Änderungen."
              : "\"Only change the function calculate_total, not the rest of the file\" — prevents unwanted changes."}
          />
          <Tip emoji="📋" title={de ? "Checklisten nutzen" : "Use Checklists"}
            desc={de
              ? "\"Prüfe folgende Punkte: 1. SQL Injection, 2. XSS, 3. CSRF, 4. Auth-Bypass\" — strukturiert die Analyse."
              : "\"Check the following: 1. SQL Injection, 2. XSS, 3. CSRF, 4. Auth Bypass\" — structures the analysis."}
          />
          <Tip emoji="🔄" title={de ? "Feedback geben" : "Give Feedback"}
            desc={de
              ? "\"Das ist gut, aber mach die Fehlerbehandlung robuster\" ist besser als komplett neu anfangen."
              : "\"That's good, but make the error handling more robust\" is better than starting over."}
          />
          <Tip emoji="📎" title={de ? "Dateien hochladen statt beschreiben" : "Upload Files Instead of Describing"}
            desc={de
              ? "Lade Code-Dateien direkt hoch statt sie zu beschreiben. Die KI kann sie dann lesen und verstehen."
              : "Upload code files directly instead of describing them. The AI can then read and understand them."}
          />
          <Tip emoji="🎯" title={de ? "Erwartetes Ergebnis beschreiben" : "Describe Expected Result"}
            desc={de
              ? "\"Das Ergebnis soll eine Python-Funktion sein die einen String nimmt und eine Liste zurückgibt\" — klare Erwartung = klares Ergebnis."
              : "\"The result should be a Python function that takes a string and returns a list\" — clear expectation = clear result."}
          />
        </div>
      </Section>

      {/* ── Prompt-Vorlagen ── */}
      <Section title={de ? "Prompt-Vorlagen zum Kopieren" : "Prompt Templates"} icon={BookOpen}>
        <div className="grid gap-2 mt-3 sm:grid-cols-2">
          <Template
            label={de ? "Datei analysieren" : "Analyze File"}
            prompt={de
              ? "Lies die Datei [DATEINAME] und erstelle eine Zusammenfassung: Zweck, Hauptfunktionen, Abhängigkeiten, mögliche Probleme."
              : "Read the file [FILENAME] and create a summary: purpose, main functions, dependencies, potential issues."}
          />
          <Template
            label={de ? "Bug finden" : "Find Bug"}
            prompt={de
              ? "In [DATEI] Zeile [NUMMER] tritt folgender Fehler auf: [FEHLERMELDUNG]. Das erwartete Verhalten ist [BESCHREIBUNG]. Finde die Ursache und schlage einen Fix vor."
              : "In [FILE] line [NUMBER], the following error occurs: [ERROR MESSAGE]. Expected behavior is [DESCRIPTION]. Find the cause and suggest a fix."}
          />
          <Template
            label={de ? "Code Review" : "Code Review"}
            prompt={de
              ? "Prüfe die Datei [DATEINAME] auf: 1. Sicherheitslücken, 2. Performance-Probleme, 3. Best-Practice-Verstöße, 4. Fehlende Fehlerbehandlung. Antworte als nummerierte Liste."
              : "Review the file [FILENAME] for: 1. Security vulnerabilities, 2. Performance issues, 3. Best practice violations, 4. Missing error handling. Answer as a numbered list."}
          />
          <Template
            label={de ? "Funktion schreiben" : "Write Function"}
            prompt={de
              ? "Schreibe eine [SPRACHE]-Funktion die [BESCHREIBUNG]. Parameter: [PARAMETER]. Rückgabe: [RÜCKGABETYP]. Bitte mit Fehlerbehandlung und Docstring."
              : "Write a [LANGUAGE] function that [DESCRIPTION]. Parameters: [PARAMETERS]. Returns: [RETURN TYPE]. Please include error handling and docstring."}
          />
          <Template
            label={de ? "Daten verarbeiten" : "Process Data"}
            prompt={de
              ? "Lies die Datei [DATEINAME] und [AKTION: sortiere/filtere/gruppiere] die Daten nach [KRITERIUM]. Ausgabeformat: [Tabelle/JSON/CSV]."
              : "Read the file [FILENAME] and [ACTION: sort/filter/group] the data by [CRITERIA]. Output format: [table/JSON/CSV]."}
          />
          <Template
            label={de ? "Vergleich erstellen" : "Create Comparison"}
            prompt={de
              ? "Vergleiche [OPTION A] und [OPTION B] für [ANWENDUNGSFALL]. Kriterien: [LISTE]. Antwort als Vergleichstabelle mit Empfehlung."
              : "Compare [OPTION A] and [OPTION B] for [USE CASE]. Criteria: [LIST]. Answer as a comparison table with recommendation."}
          />
        </div>
      </Section>

      {/* ���─ Module 3: HydraHive-spezifisch ── */}
      <Section title={de ? "HydraHive meistern" : "Mastering HydraHive"} icon={Settings2}>
        <div className="space-y-5 mt-3 text-sm">
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
              {de ? "Soul.md schreiben" : "Writing Soul.md"}
            </p>
            <div className="space-y-3">
              <Tip emoji="🎭" title={de ? "Rolle klar definieren" : "Define the Role Clearly"}
                desc={de
                  ? "\"Du bist ein DevOps-Spezialist der ...\" — gibt der KI eine klare Identität und Expertise."
                  : "\"You are a DevOps specialist who...\" — gives the AI a clear identity and expertise."}
              />
              <Tip emoji="🚧" title={de ? "Grenzen setzen" : "Set Boundaries"}
                desc={de
                  ? "\"Du darfst KEINE Dateien außerhalb von /projects/ ändern\" — verhindert ungewollte Aktionen."
                  : "\"You MUST NOT modify any files outside /projects/\" — prevents unwanted actions."}
              />
              <Tip emoji="🗣️" title={de ? "Ton vorgeben" : "Define the Tone"}
                desc={de
                  ? "\"Antworte kurz und technisch, keine Smalltalk\" — steuert Stil und Länge der Antworten."
                  : "\"Reply concisely and technically, no small talk\" — controls response style and length."}
              />
              <Tip emoji="🔧" title={de ? "Tools steuern" : "Control Tool Usage"}
                desc={de
                  ? "\"Nutze immer zuerst file_read bevor du file_write verwendest\" — legt Reihenfolge und Sorgfalt fest."
                  : "\"Always use file_read before file_write\" — enforces order and care when accessing files."}
              />
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
              {de ? "Skills richtig aufbauen" : "Building Skills Correctly"}
            </p>
            <div className="space-y-3">
              <Tip emoji="⚡" title="scope: always vs. on-demand"
                desc={de
                  ? "always für Kern-Wissen das immer gebraucht wird, on-demand für Spezialthemen — spart Token-Budget."
                  : "always for core knowledge needed every time, on-demand for specialty topics — saves token budget."}
              />
              <Tip emoji="🏷️" title={de ? "Trigger-Keywords bewusst wählen" : "Choose Trigger Keywords Deliberately"}
                desc={de
                  ? "Spezifische Keywords wie \"docker\", \"deployment\" statt generisches \"hilf mir\" — verhindert versehentliches Laden."
                  : "Specific keywords like \"docker\", \"deployment\" instead of generic \"help me\" — prevents accidental loading."}
              />
              <Tip emoji="📊" title={de ? "Skill-Priorität nutzen" : "Use Skill Priority"}
                desc={de
                  ? "Niedrigere Zahl = wird zuerst geladen. Grundlagen auf 10, Spezial-Skills auf 50+."
                  : "Lower number = loaded first. Foundation skills at 10, specialty skills at 50+."}
              />
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
              {de ? "Memory effektiv nutzen" : "Using Memory Effectively"}
            </p>
            <div className="space-y-3">
              <Tip emoji="✅" title={de ? "Was rein soll" : "What Belongs in Memory"}
                desc={de
                  ? "Entscheidungen, Kontext, Projektstatus — Dinge die beim nächsten Start relevant sind."
                  : "Decisions, context, project status — things relevant at the next session start."}
              />
              <Tip emoji="❌" title={de ? "Was NICHT rein soll" : "What Does NOT Belong"}
                desc={de
                  ? "Code-Snippets, Debug-Infos, Log-Ausgaben — veralten schnell und fressen Token."
                  : "Code snippets, debug info, log output — these go stale quickly and waste tokens."}
              />
            </div>
            <Example
              bad={de ? "Speichere dir den ganzen Code" : "Save the entire code for yourself"}
              good={de
                ? "Merke dir: Wir haben uns für PostgreSQL statt SQLite entschieden weil wir Multi-User brauchen. Migration am 2024-01-15 durchgeführt."
                : "Remember: We chose PostgreSQL over SQLite because we need multi-user support. Migration done on 2024-01-15."}
            />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Execution Modes</p>
            <div className="space-y-3">
              <Tip emoji="🟢" title="safe"
                desc={de ? "Standard-Modus, kein sudo — für normale Datei- und Code-Operationen." : "Default mode, no sudo — for normal file and code operations."} />
              <Tip emoji="🟡" title="elevated"
                desc={de ? "sudo für systemctl, chown etc. — wenn Dienste oder Berechtigungen geändert werden." : "sudo for systemctl, chown etc. — when services or permissions need changing."} />
              <Tip emoji="🔴" title="root"
                desc={de ? "Voller Zugriff — nur für Installer/Admin-Tasks, mit Bedacht einsetzen." : "Full access — only for installer/admin tasks, use with care."} />
            </div>
          </div>
        </div>
      </Section>

      {/* ── Module 4: Profi-Techniken ── */}
      <Section title={de ? "Profi-Techniken" : "Pro Techniques"} icon={Brain}>
        <div className="space-y-5 mt-3 text-sm">
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
              {de ? "Token-Budget sparen" : "Saving Token Budget"}
            </p>
            <div className="space-y-3">
              <Tip emoji="🧹" title={de ? "/clear bei Themenwechsel" : "/clear When Switching Topics"}
                desc={de
                  ? "Lange Konversationen verbrauchen Budget für alten Kontext. /clear startet frisch."
                  : "Long conversations spend budget on old context. /clear starts fresh."} />
              <Tip emoji="📄" title={de ? "Nur nötige Zeilen lesen" : "Read Only What You Need"}
                desc={de
                  ? "\"Lies nur Zeile 50–100\" statt die ganze Datei — spart erheblich Token."
                  : "\"Read only lines 50–100\" instead of the whole file — saves significant tokens."} />
              <Tip emoji="💡" title="/compact"
                desc={de
                  ? "Nutzt eine KI-Zusammenfassung um den Kontext zu schrumpfen ohne das Gespräch zu verlieren."
                  : "Uses an AI summary to shrink the context without losing the conversation thread."} />
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Multi-Agent</p>
            <div className="space-y-3">
              <Tip emoji="👑" title={de ? "Boss-Agent = Koordinator" : "Boss Agent = Coordinator"}
                desc={de
                  ? "Soul: \"Du delegierst Aufgaben an Worker. Fasse Ergebnisse zusammen und behalte den Überblick.\""
                  : "Soul: \"You delegate tasks to workers. Summarize results and keep the overview.\""} />
              <Tip emoji="⚙️" title={de ? "Worker = Spezialist" : "Worker = Specialist"}
                desc={de
                  ? "Soul: \"Du bist Experte für [X]. Antworte nur zu deinem Fachgebiet, kurz und präzise.\""
                  : "Soul: \"You are an expert in [X]. Only answer within your domain, brief and precise.\""} />
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
              {de ? "Anti-Patterns vermeiden" : "Avoid Anti-Patterns"}
            </p>
            <Example
              bad={de ? "Mach alles auf einmal: analysiere, plane, implementiere, teste" : "Do everything at once: analyze, plan, implement, test"}
              good={de
                ? "Schritt 1: Analysiere die aktuelle Struktur. Zeig mir was du findest, dann besprechen wir den Plan."
                : "Step 1: Analyze the current structure. Show me what you find, then we'll discuss the plan."} />
            <Example
              bad={de ? "Du bist der beste Programmierer der Welt" : "You are the best programmer in the world"}
              good={de
                ? "Du bist ein Python-Backend-Entwickler. Du nutzt FastAPI, SQLAlchemy und pytest."
                : "You are a Python backend developer. You use FastAPI, SQLAlchemy, and pytest."} />
            <Tip emoji="⚠️" title={de ? "\"Mach es besser\" ist kein Prompt" : "\"Make it better\" is not a prompt"}
              desc={de
                ? "Sag WAS besser sein soll: \"Mach die Fehlerbehandlung robuster: alle Exceptions loggen, Fallback-Wert zurückgeben.\""
                : "Say WHAT should be better: \"Make error handling more robust: log all exceptions, return a fallback value.\""} />
          </div>
        </div>
      </Section>
    </div>
  );
}

/* ── Helper Components ─���──────────────────────────────────────── */

function Rule({ n, title, desc }: { n: number; title: string; desc: string }) {
  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 text-primary text-sm font-bold shrink-0">
        {n}
      </div>
      <div>
        <p className="font-medium text-sm">{title}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
      </div>
    </div>
  );
}

function Tip({ emoji, title, desc }: { emoji: string; title: string; desc: string }) {
  return (
    <div className="flex gap-3 items-start">
      <span className="text-lg shrink-0">{emoji}</span>
      <div>
        <p className="font-medium">{title}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
      </div>
    </div>
  );
}
