import { describe, expect, it } from "vitest";

import { priorityTone } from "./alerts";
import { niceTicks } from "./components/ForecastChart";

describe("alert tones", () => {
  it("keeps red for security only", () => {
    expect(priorityTone({ priority: "urgent", segment: "security" })).toBe("bad");
    expect(priorityTone({ priority: "urgent", segment: "cash" })).toBe("warn");
    expect(priorityTone({ priority: "important", segment: "money" })).toBe("info");
    expect(priorityTone({ priority: "info", segment: "customers" })).toBe("neutral");
  });
});

describe("chart ticks", () => {
  it("picks round steps that cover the range", () => {
    const ticks = niceTicks(-650_600, 4_000_000);
    expect(ticks[0]).toBeLessThanOrEqual(0);
    expect(ticks[ticks.length - 1]).toBeLessThanOrEqual(4_000_000);
    const steps = new Set(ticks.slice(1).map((t, i) => t - ticks[i]));
    expect(steps.size).toBe(1);
  });
});
