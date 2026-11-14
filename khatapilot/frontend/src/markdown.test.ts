import { describe, expect, it } from "vitest";

import { inlineSpans, plainReply, replyLines } from "./markdown";

describe("reply markdown", () => {
  it("keeps bold as data, never markup", () => {
    expect(inlineSpans("Pay **Meghna** now")).toEqual([
      { text: "Pay ", bold: false },
      { text: "Meghna", bold: true },
      { text: " now", bold: false },
    ]);
    expect(inlineSpans("<img src=x onerror=alert(1)>")).toEqual([{ text: "<img src=x onerror=alert(1)>", bold: false }]);
  });
  it("drops an unpaired marker", () => {
    expect(inlineSpans("half **bold")).toEqual([{ text: "half bold", bold: false }]);
  });
  it("finds bullets but not a bold line", () => {
    const lines = replyLines("Moves:\n- Collect baki\n**Total** ৳2,300");
    expect(lines.map((l) => l.bullet)).toEqual([false, true, false]);
    expect(plainReply("Moves:\n- Collect baki\n\n- Ask Rahim")).toBe("Moves: • Collect baki • Ask Rahim");
  });
});
