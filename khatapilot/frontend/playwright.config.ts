import { defineConfig } from "@playwright/test";

// End-to-end smoke tests of the riskiest screens (approve with the PIN, STOP, savings and growth), in the
// installed Microsoft Edge so no browser download is needed. Run them with `python scripts/run_e2e.py`:
// it starts a separate sandbox, backend and frontend on spare ports with their own data, seeds the demo
// shop, runs these tests and stops everything, so the developer's own servers and var/ are never touched.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1, // one shop, one simulated clock: the flows share state, so they run in order
  reporter: [["list"]],
  use: {
    baseURL: process.env.KP_E2E_URL ?? "http://localhost:5174",
    channel: "msedge",
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
  },
});
