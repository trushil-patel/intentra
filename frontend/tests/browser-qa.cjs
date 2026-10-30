const { chromium } = require("playwright");
const fs = require("node:fs/promises");
(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:5173");
  await page.getByRole("heading", { name: /Your computer/ }).waitFor();
  await page.screenshot({
    path: "artifacts/preview-desktop.png",
    fullPage: true,
  });
  const results = [];
  for (const width of [1440, 1024, 800, 600, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    results.push({
      width,
      overflow: await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    });
  }
  await page.screenshot({
    path: "artifacts/preview-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.screenshot({ path: "artifacts/preview-settings.png" });
  console.log(JSON.stringify({ results, errors }, null, 2));
  await fs.writeFile(
    "artifacts/browser-qa.json",
    JSON.stringify({ results, errors }, null, 2),
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
