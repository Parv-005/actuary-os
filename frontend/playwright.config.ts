import { defineConfig } from "@playwright/test";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 120_000,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
  },
  webServer: [
    {
      command: "npm run dev -- --port 3000",
      port: 3000,
      reuseExistingServer: true,
      env: { NEXT_PUBLIC_API_URL: API_URL },
      timeout: 120_000,
    },
  ],
});
