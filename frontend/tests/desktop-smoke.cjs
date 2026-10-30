const { _electron: electron } = require("playwright");
const path = require("node:path");
const fs = require("node:fs/promises");
const assert = require("node:assert/strict");
(async () => {
  const artifacts = path.resolve(__dirname, "../artifacts");
  await fs.mkdir(artifacts, { recursive: true });
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const app = await electron.launch({
    args: [path.resolve(__dirname, "..")],
    env,
    timeout: 30000,
  });
  const report = [];
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.getByRole("heading", { name: /Your computer/ }).waitFor();
    report.push("Production renderer loads");
    await page.screenshot({
      path: path.join(artifacts, "command-center.png"),
      fullPage: true,
    });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    report.push("No horizontal overflow at native startup size");
    await page.getByRole("button", { name: "Floating mode" }).click();
    const overlay = await app
      .waitForEvent("window", { timeout: 3000 })
      .catch(() => app.windows().find((p) => p.url().includes("overlay=1")));
    assert(overlay);
    await overlay.waitForLoadState();
    const floating = await app.evaluate(({ BrowserWindow }) => {
      const w = BrowserWindow.getAllWindows().find((w) => w.isAlwaysOnTop());
      return { focusable: w.isFocusable(), visible: w.isVisible() };
    });
    assert(floating.visible && !floating.focusable);
    report.push("Native floating window visible, always-on-top, non-focusable");
    await overlay.screenshot({ path: path.join(artifacts, "floating.png") });
    await overlay.getByRole("button", { name: "Expand overlay" }).click();
    await new Promise((r) => setTimeout(r, 200));
    assert.equal(
      (
        await app.evaluate(({ BrowserWindow }) =>
          BrowserWindow.getAllWindows()
            .find((w) => w.isAlwaysOnTop())
            .getSize(),
        )
      )[1],
      440,
    );
    report.push("Overlay expands");
    await overlay.getByRole("button", { name: "Hide overlay" }).click();
    await new Promise((r) => setTimeout(r, 200));
    assert.equal(
      await app.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()
          .find((w) => w.isAlwaysOnTop())
          .isVisible(),
      ),
      false,
    );
    report.push("Overlay hides without app exit");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("dialog").waitFor();
    await page.screenshot({ path: path.join(artifacts, "settings.png") });
    await page.getByRole("button", { name: "Close settings" }).click();
    report.push("Settings opens and closes");
    await page.keyboard.press("Control+k");
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Escape");
    report.push("Quick actions keyboard access");
    await page.getByRole("button", { name: "Start voice agent" }).click();
    report.push("Requested real Python startup");
    await page.waitForFunction(
      async () => {
        const s = await window.intentra.snapshot();
        return (
          s.current?.type === "ready" ||
          s.current?.type === "startup_error" ||
          (!s.running && s.error)
        );
      },
      {},
      { timeout: 120000 },
    );
    const ready = await page.evaluate(() => window.intentra.snapshot());
    await fs.writeFile(
      path.join(artifacts, "startup-result.json"),
      JSON.stringify(ready, null, 2),
    );
    assert.equal(ready.current?.status, "idle", JSON.stringify(ready));
    report.push("Actual Parakeet agent reaches READY");
    await page.reload();
    await page.getByText("Agent connected", { exact: true }).waitFor();
    const afterReload = await page.evaluate(() => window.intentra.snapshot());
    assert.equal(afterReload.current.runId, ready.current.runId);
    report.push("Renderer reload reattaches to the same Python process");
    await page.screenshot({
      path: path.join(artifacts, "connected.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: /Emergency stop/ }).click();
    await page.getByText("Emergency stopped", { exact: true }).waitFor();
    await page.waitForFunction(
      async () => !(await window.intentra.snapshot()).running,
      {},
      { timeout: 15000 },
    );
    report.push("UI stop reaches Python emergency latch and process exits");
    await page.screenshot({
      path: path.join(artifacts, "emergency.png"),
      fullPage: true,
    });
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await fs.writeFile(
      path.join(artifacts, "desktop-report.json"),
      JSON.stringify(report, null, 2),
    );
    await app.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
