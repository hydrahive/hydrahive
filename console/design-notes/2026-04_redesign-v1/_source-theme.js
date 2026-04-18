/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Surface-Farben (Hintergründe, Cards, Borders)
        surface: {
          base: '#0D0B1F',   // Haupthintergrund
          raised: '#14102A', // Sidebar, Panels
          card: '#1A1633',   // Cards, Inputs
          hover: '#231C42',  // Hover-States
          border: '#2A2347', // Standard-Borders
          divider: '#1F1A38',// Subtile Trennlinien
        },

        // Agent-Type-Farben (semantisch)
        // Jede hat eine Haupt-Farbe (DEFAULT) + hellere/dunklere Varianten
        boss: {
          DEFAULT: '#7F77DD', // Lila – Boss-Agenten, Brand-Primary
          soft:    '#AFA9EC', // Text auf dunklem Grund
          mute:    '#CECBF6', // Sehr heller Text / Badges
          deep:    '#3C3489', // Badge-Background / Akzent
          ink:     '#EEEDFE', // Highlight-Text
        },
        worker: {
          DEFAULT: '#5DCAA5', // Teal – aktive Worker, "Online"-Status
          soft:    '#9FE1CB',
          deep:    '#0F6E56',
          ink:     '#E1F5EE',
        },
        personal: {
          DEFAULT: '#ED93B1', // Pink – persönliche Agenten
          soft:    '#F4C0D1',
          deep:    '#72243E',
          ink:     '#FBEAF0',
        },
        bridge: {
          DEFAULT: '#EF9F27', // Amber – Support/externe Bridges (Matrix, Discord)
          soft:    '#FAC775',
          deep:    '#854F0B',
          ink:     '#FAEEDA',
        },

        // Text-Hierarchie
        ink: {
          DEFAULT: '#EEEDFE', // Primär-Text auf dunklem Grund
          muted:   '#B4B2A9', // Sekundär-Text (Labels, Meta)
          subtle:  '#888780', // Hints, Timestamps
          faint:   '#5F5E5A', // Sehr zurückhaltend (Icon-Ränder)
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // kompakte Scale für Admin-UI
        'xs':   ['11px', '1.5'],
        'sm':   ['12px', '1.5'],
        'base': ['13px', '1.6'],
        'md':   ['14px', '1.6'],
        'lg':   ['16px', '1.5'],
        'xl':   ['20px', '1.4'],
        '2xl':  ['24px', '1.3'],
      },
      borderRadius: {
        'sm':  '4px',
        'DEFAULT': '6px',
        'md':  '8px',
        'lg':  '12px',
        'xl':  '16px',
      },
      boxShadow: {
        // dezent – keine Neon-Glows, keine harten Schatten
        'card':  '0 1px 2px rgba(0,0,0,0.3)',
        'pop':   '0 4px 16px rgba(0,0,0,0.4)',
        'focus': '0 0 0 2px rgba(127,119,221,0.4)', // Boss-Lila als Focus-Ring
      },
      spacing: {
        // für die Sidebar-Breite
        'sidebar': '220px',
        'sidebar-sm': '56px',
      },
    },
  },
  plugins: [],
};
