import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTypescript,
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
    rules: {
      // These components intentionally hydrate persisted browser state and
      // poll external runtime state from effects.
      "react-hooks/set-state-in-effect": "off",
      // Root-layout pre-hydration scripts prevent theme/locale flashing in the
      // App Router and are intentionally installed before interaction.
      "@next/next/no-before-interactive-script-outside-document": "off",
    },
  },
];

export default config;
