const { test, expect } = require("@playwright/test");

test("explore, compare, explain and inspect a scored model", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#data-pill")).toHaveText("Synthetic demo");
  await page
    .getByRole("link", { name: "Neuron explorer", exact: true })
    .click();
  await expect(page.locator("#e-graph svg")).toBeVisible();
  await expect(page.locator("#e-selected")).toContainText("1001");
  await page
    .getByRole("button", { name: "Source evidence ↗", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("source_lines");
  await page
    .getByRole("button", { name: "Close evidence", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Compare releases", exact: true })
    .click();
  await page.locator("#c-before").selectOption("r1");
  await page.locator("#c-after").selectOption("r2");
  await page
    .getByRole("button", { name: "Compare releases", exact: true })
    .click();
  await expect(page.locator("#c-summary")).toContainText("104");
  await page
    .getByRole("button", { name: "Explain & trace ↗", exact: true })
    .first()
    .click();
  await expect(page.locator("#c-graph-before svg")).toBeVisible();
  await expect(page.locator("#c-graph-after svg")).toBeVisible();
  await page
    .getByRole("button", { name: "Open source provenance ↗", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "source_file.archive_path",
  );
  await page
    .getByRole("button", { name: "Close evidence", exact: true })
    .click();
  await page.getByRole("link", { name: "Change quality", exact: true }).click();
  await expect(page.locator("#q-model option")).toHaveCount(2);
  await page.locator("#q-model").selectOption({ index: 1 });
  await expect(page.locator("#q-method")).toContainText("Isolation Forest");
  await expect(page.locator("#q-results")).toContainText("Threshold baseline");
  expect(errors).toEqual([]);
});

test("responsive navigation and the source panel work without horizontal page overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#data-pill")).toHaveText("Synthetic demo");
  await page.getByRole("link", { name: "How it works", exact: true }).click();
  await expect(page.locator("#tab-guide")).toContainText("Neuron explorer");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
  await page
    .getByRole("link", { name: "Neuron explorer", exact: true })
    .click();
  await expect(page.locator("#e-graph svg")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    ),
  ).toBe(false);
});
