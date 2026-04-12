import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const plugins: any[] = [react()];
// Bundle-Analyse: npm run analyze → öffnet stats.html
if (process.env.ANALYZE) {
  try {
    const { visualizer } = require("rollup-plugin-visualizer");
    plugins.push(visualizer({ open: true, gzipSize: true, filename: "stats.html" }));
  } catch { /* rollup-plugin-visualizer nicht installiert — ignorieren */ }
}

export default defineConfig({
  plugins,
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  build: {
    // #604: react-force-graph-3d ist bewusst ~1.3MB (nutzt three.js), wird via
    // lazy()-Import nur bei /brain nachgeladen. Warning hochsetzen, Splitting bleibt.
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("react-force-graph") || id.includes("three") || id.includes("3d-force-graph")) return "react-force-graph-3d";
          if (id.includes("emoji-picker-react")) return "emoji-picker-react";
          if (id.includes("@xyflow")) return "xyflow";
          if (id.includes("react-markdown") || id.includes("remark") || id.includes("rehype")) return "markdown";
          if (id.includes("react-router-dom") || id.includes("@remix-run")) return "router";
          if (id.includes("react-dom") || id.includes("/react/")) return "react-vendor";
          if (id.includes("lucide-react")) return "icons";
        },
      },
    },
  },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } } },
});
