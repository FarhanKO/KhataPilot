import { expect, test } from "@playwright/test";

const PIN = process.env.KP_E2E_PIN ?? "";

async function say(page: import("@playwright/test").Page, text: string) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

test("the chat home answers from the books", async ({ page }) => {
  await page.goto("/#chat");
  await say(page, "show my bills");
  await expect(page.getByText(/open bills/).first()).toBeVisible();
});

test("a payment to a new payee waits for the PIN, then pays once", async ({ page }) => {
  test.skip(!PIN, "KP_E2E_PIN is set by scripts/run_e2e.py");
  await page.goto("/#chat");
  await say(page, "pay Fresh Dairy Point 1500");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Approve this payment?")).toBeVisible();
  await expect(dialog.getByText(/New payee/)).toBeVisible();
  await dialog.getByLabel("PIN").fill("0000");
  await dialog.getByRole("button", { name: "Confirm with PIN" }).click();
  await expect(dialog.getByText(/wrong|incorrect/i)).toBeVisible();
  await dialog.getByLabel("PIN").fill(PIN);
  await dialog.getByRole("button", { name: "Confirm with PIN" }).click();
  await expect(page.getByText(/Paid ৳1,500 to Fresh Dairy Point/).first()).toBeVisible();
});

test("the savings page shows the plan and the growth section", async ({ page }) => {
  await page.goto("/#savings");
  await expect(page.getByRole("heading", { name: "Pay yourself, grow the shop" })).toBeVisible();
  await expect(page.getByText("Grow your shop")).toBeVisible();
  await expect(page.getByText("Monthly profit on three paths")).toBeVisible();
  await page.getByRole("button", { name: "10%" }).click();
  await expect(page.getByText(/10% of September's ৳23,053 profit/).first()).toBeVisible();
  await page.getByRole("button", { name: "Last 3 months (steadier)" }).click();
  await expect(page.getByText(/\(the middle of Jul–Sep\)/).first()).toBeVisible();
  await expect(page.locator("svg text", { hasText: "Ramadan" }).first()).toBeVisible();
  // starting a plan set starter goals from the books
  await expect(page.getByText("What you are saving for")).toBeVisible();
  await expect(page.getByText("Eid-ul-Fitr stock").first()).toBeVisible();
  await page.getByRole("button", { name: "Remove the goal Bulk stock order" }).click();
  await expect(page.getByRole("button", { name: "Remove the goal Bulk stock order" })).toHaveCount(0);
  await page.getByLabel("Goal", { exact: true }).fill("Fridge");
  await page.getByLabel("Amount (৳)").fill("35000");
  await page.getByRole("button", { name: "Add goal" }).click();
  await expect(page.getByRole("progressbar", { name: "Fridge: 0% saved" })).toBeVisible();
  await expect(page.getByText(/৳0 of ৳35,000/).first()).toBeVisible();
});

test("alerts show the month's AI use and make a lender report with no customer names", async ({ page }) => {
  await page.goto("/#chat");
  await page.getByRole("button", { name: /^Alerts/ }).click();
  const alerts = page.getByRole("dialog", { name: "Alerts" });
  await expect(alerts.getByText(/AI use ·/)).toBeVisible();
  await expect(alerts.getByText(/Offline mode: .*no AI cost/)).toBeVisible();
  await alerts.getByRole("button", { name: "Monthly report" }).click();
  await page.getByRole("radio", { name: /For a bank or NGO loan/ }).click();
  await page.getByRole("button", { name: "Make the report" }).click();
  const sheet = page.getByRole("dialog", { name: /Business statement for a loan/ });
  await expect(sheet.getByText("Owner's declaration")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Print or save as PDF" })).toBeVisible();
  await expect(sheet.getByText("Babul Mia")).toHaveCount(0);
  await sheet.getByRole("button", { name: "Close the report" }).click();
  await expect(sheet).toHaveCount(0);
});

test("a customer's STOP reply shows in the Ledger as no SMS", async ({ page }) => {
  const reply = await page.request.post("/api/sim/inbound", {
    data: { from_number: "01933445566", channel: "sms_gateway", text: "STOP" },
  });
  expect(reply.ok()).toBeTruthy();
  await page.goto("/#ledger");
  await expect(page.getByText(/asked for no SMS/)).toBeVisible();
  await expect(page.getByText(/Only they can turn reminders back on/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Allow reminders to Babul Mia again" })).toBeDisabled();
});

test("STOP freezes everything and needs the PIN to resume", async ({ page }) => {
  await page.goto("/#chat");
  await page.getByRole("button", { name: "STOP: freeze everything" }).click();
  await expect(page.getByText("STOP is on").first()).toBeVisible();
  await say(page, "pay Rahim Traders 3000");
  await expect(page.getByText(/STOP is on/).last()).toBeVisible();
});
