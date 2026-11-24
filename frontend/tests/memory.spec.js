const { test, expect } = require("@playwright/test");

test("condition the real circuit, branch its memory, and replay the paired arena", async ({ page, request }) => {
  const response = await request.get("/api/v1/memory/runs");
  test.skip(!response.ok() || !(await response.json()).length, "Requires a loaded FlyWire memory circuit");

  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("link", { name: "Memory Lab", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Odor Memory Lab" })).toBeVisible();
  await expect(page.locator("#digest")).toContainText("SHA-256");
  await page.getByLabel("Odor-pair seed").fill("219");
  await page.getByRole("button", { name: "Run conditioning and controls" }).click();
  await expect(page.locator("#status")).toContainText("seed 219", { timeout: 20000 });
  await expect(page.locator("#metrics tr")).toHaveCount(9);
  const parentHash = await page.locator("#digest").textContent();

  await page.getByLabel("Intervention at this encounter").selectOption("paired_B");
  await page.getByRole("button", { name: "Fork from here" }).click();
  await expect(page.locator("#compare")).toContainText("sustained preference for B");
  await expect(page.locator("#digest")).toHaveText(parentHash);
  await expect(page.locator("#store-summary")).toContainText("Same state");
  await page.getByRole("button", {name: /Final test/}).click();
  await expect(page.locator("#event")).toContainText("Trial 80");
  await expect(page.locator("#odor-cards")).toContainText("active Kenyon cells");
  await expect(page.locator("#choice-detail")).toContainText("Recorded choice");
  await expect(page.locator("#update-detail")).toContainText("Learning pauses during tests");
  await expect(page.locator("#edges tr")).toHaveCount(8);
  await expect(page.locator("#mixtures tr")).toHaveCount(5);

  await page.getByLabel("Navigation condition").selectOption("memory_only");
  await expect(page.locator("#route-reference")).toBeVisible();
  await page.getByLabel("Arena frame").fill("8");
  await expect(page.locator("#arenaText")).toContainText("frame 9");
  await page.getByRole("button", { name: "Play route" }).click();
  await expect(page.locator("#arenaText")).toContainText("target B");
  await expect(page.locator("#arenaText")).toContainText("frame 32");
  const replay = await request.get(await page.locator("#arenaExport").getAttribute("href"));
  const arena = await replay.json();
  expect(arena.conditions).toHaveLength(4);
  expect(arena.conditions[2].trials.flatMap(t => t.steering).every(x => x === 0)).toBeTruthy();

  await page.getByRole("button", { name: "Benchmark PostgreSQL replay" }).click();
  await expect(page.locator("#cost")).toContainText("45 matching state hashes");
  await expect(page.locator("#storage tr")).toHaveCount(5);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

