import { expect, test } from "@playwright/test";

/**
 * Judge demonstration flow, deterministic part (§25 steps 1–5 + audit):
 * dashboard → guided demo → CP-1 select v2 → CP-2 accept with rationale →
 * validation shows ACCEPTED_EXCEPTION → audit shows the decisions.
 * (Insight/LLM stages need a scripted provider, covered by backend tests.)
 */
test("guided demo drives CP-1 and CP-2 from the browser", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("MPR-2026-08-001")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "Start Guided Demo" }).click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]+/, { timeout: 30_000 });

  // CP-1: duplicate claims submission (modal auto-opens on blocking)
  const cp1Title = page.getByRole("heading", {
    name: /Two claims files found/,
  });
  await expect(cp1Title).toBeVisible({ timeout: 60_000 });
  await page.getByRole("radio", { name: /v2\.csv/ }).check();
  await page.getByRole("button", { name: "Submit decision" }).click();
  await expect(cp1Title).toBeHidden({ timeout: 30_000 });

  // CP-2: validation blocker (prep + validation run first; the modal
  // only opens after clicking Decide — unlike CP-1 it is not auto-opened)
  await expect(
    page.getByText("Validation blocked").first()
  ).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: "Decide" }).click();
  const cp2Title = page.getByRole("heading", { name: /Validation blocked/ });
  await expect(cp2Title).toBeVisible();
  await page.getByRole("combobox").selectOption("accept_exception");
  await page
    .getByPlaceholder(/Known endorsement/)
    .fill("Known endorsement processing lag — documented");
  await page.getByRole("button", { name: "Submit decision" }).click();
  await expect(cp2Title).toBeHidden({ timeout: 60_000 });

  // validation tab carries the accepted exception
  await page.getByRole("button", { name: "validation" }).click();
  await expect(page.getByText("ACCEPTED_EXCEPTION").first()).toBeVisible({
    timeout: 60_000,
  });

  // audit tab records both human decisions
  await page.getByRole("button", { name: "audit" }).click();
  await expect(page.getByText("decision select file").first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("decision accept exception").first()).toBeVisible();
});
