import { describe, expect, it } from "vitest";

import { compactTaka, dayLabel, greeting, maskNumber, taka, withAttempts } from "./format";

describe("money", () => {
  it("shows paisa as taka", () => {
    expect(taka(1_250_050)).toBe("৳12,500.5");
    expect(taka(1_250_050, true)).toBe("৳12,501");
    expect(taka(-200_000, true)).toBe("-৳2,000");
    expect(taka(null)).toBe("—");
  });
  it("shortens big amounts for chart axes", () => {
    expect(compactTaka(3_300_000)).toBe("৳33k");
    expect(compactTaka(1_250_000)).toBe("৳12.5k");
    expect(compactTaka(50_000)).toBe("৳500");
  });
});

describe("numbers and dates", () => {
  it("masks phone numbers and bank accounts", () => {
    expect(maskNumber("01711000222")).toBe("017XXXX0222");
    expect(maskNumber("+880 1711-000222")).toBe("017XXXX0222");
    expect(maskNumber("1701500012345")).toBe("****2345");
    expect(maskNumber(null)).toBe("");
  });
  it("reads dates in Dhaka time", () => {
    expect(dayLabel("2026-10-12")).toBe("Mon 12 Oct");
    expect(greeting("2026-10-12T04:00:00Z")).toBe("Good morning"); // 10:00 in Dhaka
    expect(greeting("2026-10-12T17:00:00Z")).toBe("Good night"); // 23:00 in Dhaka
  });
  it("adds the PIN tries left only once", () => {
    expect(withAttempts("Wrong PIN.", 2)).toBe("Wrong PIN. (2 tries left)");
    expect(withAttempts("Wrong PIN, 2 tries left.", 2)).toBe("Wrong PIN, 2 tries left.");
  });
});
