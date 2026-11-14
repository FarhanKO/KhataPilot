import { defineConfig } from "vitest/config";

// Unit tests for the frontend's pure logic (formatting, reply markdown, alert tones, chart ticks).
// Browser flows are covered by the end-to-end smoke tests in e2e/ (scripts/run_e2e.py).
export default defineConfig({
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
