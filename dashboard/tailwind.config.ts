import type { Config } from "tailwindcss";

/**
 * ECONITH theme tokens via CSS variables (light + dark).
 * Flat solid surfaces only — no gradients.
 */
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./contexts/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        base: "var(--color-base)",
        surface: "var(--color-surface)",
        elevated: "var(--color-elevated)",
        line: "var(--color-line)",
        ink: "var(--color-ink)",
        muted: "var(--color-muted)",
        faint: "var(--color-faint)",
        accent: "#3b82f6",
        world: "#10b981",
        warn: "#f59e0b",
        danger: "#ef4444",
        ok: "#22c55e",
        // Sovereign Trading OS — directional signal semantics
        long: "var(--wr-long)",
        short: "var(--wr-short)",
        flat: "var(--wr-flat)",
        // War-room accent rails per hierarchy zone
        "zone-alpha": "var(--wr-zone-alpha)",
        "zone-exec": "var(--wr-zone-exec)",
        "zone-risk": "var(--wr-zone-risk)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        xl: "0.625rem",
      },
      keyframes: {
        "wr-flash": {
          "0%": { backgroundColor: "rgba(59,130,246,0.16)" },
          "100%": { backgroundColor: "transparent" },
        },
        "wr-sweep": {
          "0%": { transform: "translateX(-120%)" },
          "100%": { transform: "translateX(320%)" },
        },
        "wr-dash": {
          to: { strokeDashoffset: "-16" },
        },
      },
      animation: {
        "wr-flash": "wr-flash 700ms ease-out",
        "wr-sweep": "wr-sweep 2.4s ease-in-out infinite",
        "wr-dash": "wr-dash 0.9s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
