import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("react-markdown")) return "markdown";
          if (id.includes("react-router-dom") || id.includes("@remix-run")) return "router";
          if (id.includes("react-dom") || id.includes("/react/")) return "react-vendor";
          if (id.includes("lucide-react")) return "icons";
        },
      },
    },
  },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } } },
});
