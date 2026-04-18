import "@/lib/i18n";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "@/hooks/useAuth";
import App from "./App";
import "./index.css";

// #720 Mobile-Chat: visualViewport-Listener setzt --vv-height auf die aktuell
// sichtbare Viewport-Höhe. Wenn iOS-Tastatur aufgeht, schrumpft vv.height —
// die .h-viewport-safe-Utility in index.css liest diesen Wert und hält den
// Chat-Container auf der sichtbaren Fläche. Harmloser Noop wenn die API fehlt.
(function installVisualViewportSync() {
  const vv = typeof window !== "undefined" ? window.visualViewport : null;
  if (!vv) return;
  const apply = () => {
    document.documentElement.style.setProperty("--vv-height", `${vv.height}px`);
  };
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  apply();
})();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
// force-rebuild-1774356870
