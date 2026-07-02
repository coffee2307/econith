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
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        xl: "0.625rem",
      },
    },
  },
  plugins: [],
};

export default config;
