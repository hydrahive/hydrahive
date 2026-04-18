import "@/lib/i18n";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "@/hooks/useAuth";
import App from "./App";
import "./index.css";

// #725: visualViewport-Listener für Mobile-Keyboard-Awareness.
// Setzt --vv-height als aktuelle visualViewport.height; .h-viewport-safe
// nutzt das, damit der App-Shell mit der Bildschirmtastatur schrumpft.
// Graceful Noop in Browsern ohne visualViewport-API (z.B. sehr altes IE).
if (typeof window !== "undefined" && window.visualViewport) {
  const vv = window.visualViewport;
  const syncHeight = () => {
    document.documentElement.style.setProperty("--vv-height", `${vv.height}px`);
  };
  syncHeight();
  vv.addEventListener("resize", syncHeight);
  vv.addEventListener("scroll", syncHeight);
}

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
