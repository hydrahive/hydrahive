/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Bestehendes shadcn-CSS-Var-System (unverändert)
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },

        // Redesign v1 (2026-04) — siehe console/design-notes/2026-04_redesign-v1/
        // Semantische Agent-Type-Tokens für neue Components. Additive Ergänzung
        // zum bestehenden shadcn-System; keine Überschneidung der Namensräume.
        surface: {
          base: "#0D0B1F",
          raised: "#14102A",
          card: "#1A1633",
          hover: "#231C42",
          border: "#2A2347",
          divider: "#1F1A38",
        },
        boss: {
          DEFAULT: "#7F77DD",
          soft: "#AFA9EC",
          mute: "#CECBF6",
          deep: "#3C3489",
          ink: "#EEEDFE",
        },
        worker: {
          DEFAULT: "#5DCAA5",
          soft: "#9FE1CB",
          deep: "#0F6E56",
          ink: "#E1F5EE",
        },
        personal: {
          DEFAULT: "#ED93B1",
          soft: "#F4C0D1",
          deep: "#72243E",
          ink: "#FBEAF0",
        },
        bridge: {
          DEFAULT: "#EF9F27",
          soft: "#FAC775",
          deep: "#854F0B",
          ink: "#FAEEDA",
        },
        ink: {
          DEFAULT: "#EEEDFE",
          muted: "#B4B2A9",
          subtle: "#888780",
          faint: "#5F5E5A",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // Redesign v1: neue Token, additiv (keine Kollision mit Tailwind-Defaults)
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.3)",
        pop: "0 4px 16px rgba(0,0,0,0.4)",
        focus: "0 0 0 2px rgba(127,119,221,0.4)",
      },
      spacing: {
        sidebar: "220px",
        "sidebar-sm": "56px",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
