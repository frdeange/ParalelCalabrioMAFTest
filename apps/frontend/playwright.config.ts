import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the component-local smoke e2e (issue #30).
 *
 * Covers the happy path login → ask → streamed reply with **no real network**:
 * MSAL is bypassed by seeding the sessionStorage token cache (see
 * `e2e/fixtures/auth.ts`) and the backend AG-UI endpoint is mocked via
 * `page.route` inside the test. Cross-component e2e lives in `tests-e2e/`.
 *
 * The `webServer` block builds and serves the app, so neither local runs nor
 * CI need to start a server manually. NEXT_PUBLIC_* values are dummy because
 * auth is seeded, not performed.
 */

const PORT = 3100;
const baseURL = `http://127.0.0.1:${PORT}`;

const buildEnv = {
  NEXT_PUBLIC_AZURE_CLIENT_ID: "e2e-client-id",
  NEXT_PUBLIC_AZURE_TENANT_ID: "e2e-tenant-id",
  NEXT_PUBLIC_REDIRECT_URI: baseURL,
  NEXT_PUBLIC_API_SCOPE: "api://calabrio-wfm/.default",
  // Same-origin as the app so the mocked /agui route has no CORS preflight.
  NEXT_PUBLIC_BACKEND_API_URL: baseURL,
  NEXT_TELEMETRY_DISABLED: "1",
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run build && npm run start -- --port 3100",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: buildEnv,
  },
});
