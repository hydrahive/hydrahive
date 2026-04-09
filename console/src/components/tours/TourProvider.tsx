/**
 * TourProvider — Leichtgewichtiges Guided Tour System (#532)
 *
 * Tooltip-basierte Step-by-Step Tours ohne externe Dependencies.
 * Tour-Fortschritt in localStorage persistiert.
 */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { X, ChevronRight, ChevronLeft, Sparkles } from "lucide-react";

// ── Tour Definitions ─────────────────────────────────────────────────────────

export interface TourStep {
  /** CSS selector for the target element to highlight */
  target: string;
  /** Title shown in tooltip */
  title: string;
  /** Description text */
  content: string;
  /** Route to navigate to before showing this step */
  route?: string;
  /** Position of tooltip relative to target */
  placement?: "top" | "bottom" | "left" | "right";
}

export interface TourDef {
  id: string;
  name: string;
  description: string;
  steps: TourStep[];
}

export const TOURS: TourDef[] = [
  {
    id: "first-start",
    name: "Dein erster Start",
    description: "Lerne die wichtigsten Bereiche von HydraHive kennen.",
    steps: [
      {
        target: '[data-tour="nav-myagent"]',
        title: "Dein Assistent",
        content: "Hier findest du deinen persönlichen KI-Assistenten. Er kennt dich und kann dir bei allem helfen.",
        route: "/",
        placement: "right",
      },
      {
        target: '[data-tour="nav-projects"]',
        title: "Projekte",
        content: "Projekte sind Arbeitsräume für Dateien und Zusammenarbeit mit spezialisierten Agenten.",
        placement: "right",
      },
      {
        target: '[data-tour="nav-settings"]',
        title: "Einstellungen",
        content: "Hier richtest du Modelle, API-Keys und Verbindungen ein.",
        placement: "right",
      },
      {
        target: '[data-tour="nav-extensions"]',
        title: "Erweiterungen",
        content: "Installiere optionale Dienste wie Web-Suche, Code-Editor oder Ollama.",
        placement: "right",
      },
    ],
  },
  {
    id: "my-assistant",
    name: "Deinen Assistenten kennenlernen",
    description: "Erfahre was dein persönlicher Agent kann.",
    steps: [
      {
        target: '[data-tour="chat-input"]',
        title: "Chat",
        content: "Schreib deinem Assistenten eine Nachricht — er antwortet in Echtzeit mit Streaming.",
        route: "/my-agent",
        placement: "top",
      },
      {
        target: '[data-tour="agent-tabs"]',
        title: "Agent-Einstellungen",
        content: "Unter den Tabs findest du Persönlichkeit, Skills, Memory und Sicherheits-Einstellungen.",
        placement: "bottom",
      },
    ],
  },
  {
    id: "first-project",
    name: "Erstes Projekt erstellen",
    description: "Erstelle einen Arbeitsraum für eine konkrete Aufgabe.",
    steps: [
      {
        target: '[data-tour="nav-projects"]',
        title: "Zur Projektseite",
        content: "Projekte bündeln Agenten, Dateien und Chat-Verläufe zu einem Thema.",
        route: "/projects",
        placement: "right",
      },
      {
        target: '[data-tour="project-create"]',
        title: "Neues Projekt",
        content: "Klicke hier um dein erstes Projekt zu erstellen. Du kannst einen Boss-Agent zuweisen der die Arbeit koordiniert.",
        placement: "bottom",
      },
    ],
  },
];

// ── Context ──────────────────────────────────────────────────────────────────

interface TourContextType {
  activeTour: TourDef | null;
  currentStep: number;
  startTour: (tourId: string) => void;
  nextStep: () => void;
  prevStep: () => void;
  endTour: () => void;
  completedTours: string[];
}

const TourContext = createContext<TourContextType>({
  activeTour: null, currentStep: 0,
  startTour: () => {}, nextStep: () => {}, prevStep: () => {}, endTour: () => {},
  completedTours: [],
});

export const useTour = () => useContext(TourContext);

// ── Provider ─────────────────────────────────────────────────────────────────

function getCompleted(): string[] {
  try { return JSON.parse(localStorage.getItem("hh_tours_completed") || "[]"); }
  catch { return []; }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [activeTour, setActiveTour] = useState<TourDef | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [completedTours, setCompletedTours] = useState<string[]>(getCompleted);

  const startTour = useCallback((tourId: string) => {
    const tour = TOURS.find(t => t.id === tourId);
    if (!tour) return;
    setActiveTour(tour);
    setCurrentStep(0);
    // Navigate to first step's route if needed
    if (tour.steps[0]?.route && location.pathname !== tour.steps[0].route) {
      navigate(tour.steps[0].route);
    }
  }, [navigate, location.pathname]);

  const endTour = useCallback(() => {
    if (activeTour) {
      const updated = [...new Set([...completedTours, activeTour.id])];
      setCompletedTours(updated);
      localStorage.setItem("hh_tours_completed", JSON.stringify(updated));
    }
    setActiveTour(null);
    setCurrentStep(0);
  }, [activeTour, completedTours]);

  const nextStep = useCallback(() => {
    if (!activeTour) return;
    if (currentStep >= activeTour.steps.length - 1) {
      endTour();
      return;
    }
    const nextIdx = currentStep + 1;
    const nextRoute = activeTour.steps[nextIdx]?.route;
    if (nextRoute && location.pathname !== nextRoute) {
      navigate(nextRoute);
    }
    setCurrentStep(nextIdx);
  }, [activeTour, currentStep, endTour, navigate, location.pathname]);

  const prevStep = useCallback(() => {
    if (currentStep > 0) {
      const prevIdx = currentStep - 1;
      const prevRoute = activeTour?.steps[prevIdx]?.route;
      if (prevRoute && location.pathname !== prevRoute) {
        navigate(prevRoute);
      }
      setCurrentStep(prevIdx);
    }
  }, [activeTour, currentStep, navigate, location.pathname]);

  return (
    <TourContext.Provider value={{ activeTour, currentStep, startTour, nextStep, prevStep, endTour, completedTours }}>
      {children}
      {activeTour && <TourOverlay />}
    </TourContext.Provider>
  );
}

// ── Tour Overlay ─────────────────────────────────────────────────────────────

function TourOverlay() {
  const { activeTour, currentStep, nextStep, prevStep, endTour } = useTour();
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!activeTour) return;
    const step = activeTour.steps[currentStep];
    if (!step) return;

    // Delay to allow route navigation to render
    const timer = setTimeout(() => {
      const el = document.querySelector(step.target);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        setTargetRect(el.getBoundingClientRect());
      } else {
        setTargetRect(null);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [activeTour, currentStep]);

  if (!activeTour) return null;
  const step = activeTour.steps[currentStep];
  if (!step) return null;

  const isLast = currentStep >= activeTour.steps.length - 1;
  const placement = step.placement || "bottom";

  // Tooltip position
  let tooltipStyle: React.CSSProperties = { position: "fixed", zIndex: 10001 };
  if (targetRect) {
    switch (placement) {
      case "right":
        tooltipStyle.left = targetRect.right + 12;
        tooltipStyle.top = targetRect.top + targetRect.height / 2;
        tooltipStyle.transform = "translateY(-50%)";
        break;
      case "left":
        tooltipStyle.right = window.innerWidth - targetRect.left + 12;
        tooltipStyle.top = targetRect.top + targetRect.height / 2;
        tooltipStyle.transform = "translateY(-50%)";
        break;
      case "top":
        tooltipStyle.left = targetRect.left + targetRect.width / 2;
        tooltipStyle.bottom = window.innerHeight - targetRect.top + 12;
        tooltipStyle.transform = "translateX(-50%)";
        break;
      case "bottom":
      default:
        tooltipStyle.left = targetRect.left + targetRect.width / 2;
        tooltipStyle.top = targetRect.bottom + 12;
        tooltipStyle.transform = "translateX(-50%)";
        break;
    }
  } else {
    // Fallback: center of screen
    tooltipStyle.left = "50%";
    tooltipStyle.top = "50%";
    tooltipStyle.transform = "translate(-50%, -50%)";
  }

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/40 z-[10000]" onClick={endTour} />

      {/* Spotlight cutout */}
      {targetRect && (
        <div className="fixed z-[10000] rounded-lg ring-4 ring-primary/50 pointer-events-none"
          style={{
            left: targetRect.left - 4, top: targetRect.top - 4,
            width: targetRect.width + 8, height: targetRect.height + 8,
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.4)",
          }}
        />
      )}

      {/* Tooltip */}
      <div style={tooltipStyle}
        className="w-72 rounded-xl border bg-card p-4 shadow-xl">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary" />
            <span className="font-semibold text-sm">{step.title}</span>
          </div>
          <button onClick={endTour} className="text-muted-foreground hover:text-foreground">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{step.content}</p>
        <div className="flex items-center justify-between mt-4">
          <span className="text-[10px] text-muted-foreground">
            {currentStep + 1} / {activeTour.steps.length}
          </span>
          <div className="flex gap-1.5">
            {currentStep > 0 && (
              <button onClick={prevStep}
                className="flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs hover:bg-muted transition-colors">
                <ChevronLeft className="w-3 h-3" /> Zurück
              </button>
            )}
            <button onClick={nextStep}
              className="flex items-center gap-1 rounded-lg bg-primary text-primary-foreground px-3 py-1 text-xs hover:bg-primary/90 transition-colors">
              {isLast ? "Fertig" : "Weiter"} {!isLast && <ChevronRight className="w-3 h-3" />}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ── Tour Launcher Button ─────────────────────────────────────────────────────

export function TourLauncher() {
  const { startTour, completedTours } = useTour();
  const [open, setOpen] = useState(false);

  const availableTours = TOURS.filter(t => !completedTours.includes(t.id));
  if (availableTours.length === 0 && !open) return null;

  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs hover:bg-muted transition-colors">
        <Sparkles className="w-3.5 h-3.5 text-primary" />
        <span>Geführte Tour</span>
        {availableTours.length > 0 && (
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] text-primary-foreground font-medium">
            {availableTours.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-1 w-64 rounded-xl border bg-card p-2 shadow-lg z-50">
          {TOURS.map(tour => {
            const done = completedTours.includes(tour.id);
            return (
              <button key={tour.id}
                onClick={() => { startTour(tour.id); setOpen(false); }}
                className="w-full text-left rounded-lg px-3 py-2 hover:bg-muted transition-colors">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{tour.name}</span>
                  {done && <span className="text-[10px] text-green-500">✓</span>}
                </div>
                <p className="text-[11px] text-muted-foreground">{tour.description}</p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
