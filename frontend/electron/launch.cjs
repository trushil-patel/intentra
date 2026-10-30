const { spawn } = require("node:child_process");
const path = require("node:path");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require("electron"), [path.resolve(__dirname, "..")], {
  env,
  stdio: "inherit",
  windowsHide: true,
  shell: false,
});
child.on("error", () => {
  console.error(
    "Unable to launch Intentra OS. Check the local Electron installation.",
  );
  process.exitCode = 1;
});
child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
