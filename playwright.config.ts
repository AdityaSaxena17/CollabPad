import { loadEnvConfig } from "@next/env";
import { defineConfig, devices } from "@playwright/test";

loadEnvConfig(process.cwd());
process.env.CLERK_PUBLISHABLE_KEY ??= process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

for (const name of [
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY",
  "E2E_OWNER_EMAIL",
  "E2E_GUEST_EMAIL",
]) {
  if (!process.env[name]) {
    throw new Error(`${name} is required for the live Clerk browser suite.`);
  }
}

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: "http://localhost:3001",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /clerk\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["setup"],
      testIgnore: /clerk\.setup\.ts/,
    },
  ],
  webServer: {
    command: "npm run dev -- --port 3001",
    url: "http://localhost:3001/sign-in",
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      FRONTEND_ORIGIN: "http://localhost:3001",
      NEXT_PUBLIC_GATEWAY_HTTP_URL: "http://localhost:18081",
      NEXT_PUBLIC_GATEWAY_WS_URL: "ws://localhost:18081",
    },
  },
});
