import { expect, test } from "@playwright/test";

/**
 * Read-only depth on the seeded COMPLETED August workflow (§25 steps 9–14):
 * findings list → drill-down evidence chain → approved report → audit timeline.
 */
test("completed workflow shows findings, report and audit", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("MPR-2026-08-001")).toBeVisible({ timeout: 30_000 });
  await page.getByText("MPR-2026-08-001").click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]+/, { timeout: 30_000 });
  await expect(page.getByText("COMPLETED").first()).toBeVisible({ timeout: 30_000 });

  // findings tab → drill-down
  await page.getByRole("button", { name: /findings/ }).click();
  const firstFinding = page.locator("a[href*='/findings/']").first();
  await expect(firstFinding).toBeVisible({ timeout: 30_000 });
  await firstFinding.click();
  await expect(page).toHaveURL(/\/findings\/[0-9a-f-]+/);
  await expect(page.getByText("Evidence chain")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Calculation:").first()).toBeVisible();
  // seeded Aug evidence links the metric + formula snapshot (scripted seed
  // carries no dataset versions — live runs link through to file downloads)
  await expect(page.getByText("Metric:").first()).toBeVisible();

  // report tab: approved + QA evidence
  await page.goto(page.url().split("/findings/")[0]);
  await page.getByRole("button", { name: "report", exact: true }).click();
  await expect(page.getByText("Executive summary")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("approved").first()).toBeVisible();

  // audit tab: full timeline incl. approval + agent costs
  await page.getByRole("button", { name: "audit" }).click();
  await expect(page.getByText("workflow completed").first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("LLM calls").first()).toBeVisible();
});
