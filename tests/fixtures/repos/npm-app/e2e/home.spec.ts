import { expect, test } from "@playwright/test";

test("adds a tide pool", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add pool" }).click();
  await expect(page.getByRole("listitem")).toHaveCount(1);
});
