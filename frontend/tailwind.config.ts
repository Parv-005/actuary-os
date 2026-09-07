import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        ink: {
          50: "#f0f4fa",
          100: "#dce6f3",
          700: "#1b3a66",
          800: "#16305a",
          900: "#0f2444",
          950: "#0a1730",
        },
      },
      boxShadow: {
        card: "0 1px 2px -1px rgb(15 36 68 / 0.08), 0 4px 14px -4px rgb(15 36 68 / 0.08)",
        pop: "0 24px 64px -16px rgb(10 23 48 / 0.35)",
      },
    },
  },
  plugins: [],
};
export default config;
