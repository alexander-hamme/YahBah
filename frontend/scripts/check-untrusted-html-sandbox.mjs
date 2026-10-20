// Usage: node scripts/check-untrusted-html-sandbox.mjs  (real-browser check that UntrustedHtmlFrame runs no script and loads nothing; needs local Chrome/Chromium, CHROME_PATH overrides)
//
// jsdom can't check this behaviour, so this loads the real srcdoc builder in headless Chromium next to an
// unsandboxed control frame carrying the same payload. The control must execute and fetch, or the result
// is inconclusive. Exit codes: 0 pass, 1 fail, 2 inconclusive or no browser.
import { execFile } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = await readFile(path.join(here, "../src/lib/untrusted-html.ts"), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { untrustedHtmlSrcDoc } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const pw = path.join(os.homedir(), "Library/Caches/ms-playwright");
  if (existsSync(pw)) {
    for (const dir of readdirSync(pw).filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse()) {
      for (const sub of readdirSync(path.join(pw, dir))) {
        const bin = path.join(pw, dir, sub, "chrome-headless-shell");
        if (existsSync(bin)) return bin;
      }
    }
  }
  return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/chromium", "/usr/bin/google-chrome"].find(
    (p) => existsSync(p)
  );
}

// Every vector carries its frame's tag, so each probe request is attributable.
const payload = (tag, origin) => `
  <h1>Thank you for applying</h1>
  <img src="${origin}/probe-${tag}-img.png" onerror="parent.postMessage('${tag}:img-onerror','*')">
  <img src="x-${tag}" onerror="parent.postMessage('${tag}:x-onerror','*')">
  <svg onload="parent.postMessage('${tag}:svg-onload','*')"></svg>
  <script>parent.postMessage('${tag}:script','*')</script>
  <link rel="stylesheet" href="${origin}/probe-${tag}-css.css">
  <div style="background-image:url(${origin}/probe-${tag}-bg.png);width:10px;height:10px"></div>
  <iframe src="${origin}/probe-${tag}-frame.html"></iframe>
  <meta http-equiv="refresh" content="0;url=${origin}/probe-${tag}-refresh.html">
  <form action="${origin}/probe-${tag}-form" method="get"><input name="q" value="1"></form>
  <script>document.forms[0] && document.forms[0].submit()</script>`;

const asScriptLiteral = (s) => JSON.stringify(s).replace(/</g, "\\u003c");

const requests = [];
let parentPage = "";
const server = http.createServer((req, res) => {
  if (req.url === "/") {
    res.writeHead(200, { "Content-Type": "text/html" }).end(parentPage);
  } else {
    requests.push(req.url);
    res.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

parentPage = `<!doctype html><html><body><pre id="out">pending</pre><script>
  const msgs = [];
  addEventListener("message", (e) => msgs.push(String(e.data)));
  function frame(attrs, srcdoc) {
    const f = document.createElement("iframe");
    for (const [k, v] of Object.entries(attrs)) f.setAttribute(k, v);
    f.srcdoc = srcdoc;
    document.body.appendChild(f);
  }
  // Exactly what UntrustedHtmlFrame renders.
  frame({ sandbox: "", referrerpolicy: "no-referrer" }, ${asScriptLiteral(untrustedHtmlSrcDoc(payload("sut", origin)))});
  frame({}, ${asScriptLiteral(payload("control", origin))});
  setTimeout(() => { document.getElementById("out").textContent = "RESULT " + JSON.stringify(msgs); }, 2500);
</script></body></html>`;

const chrome = findChrome();
if (!chrome) {
  console.error("No Chrome/Chromium found; set CHROME_PATH.");
  server.close();
  process.exit(2);
}
const profile = await mkdtemp(path.join(os.tmpdir(), "yahbah-sandbox-check-"));
let stdout = "";
try {
  ({ stdout } = await new Promise((resolve, reject) =>
    execFile(
      chrome,
      [
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        `--user-data-dir=${profile}`,
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        "--virtual-time-budget=6000",
        "--dump-dom",
        `${origin}/`,
      ],
      { timeout: 60_000 },
      (err, out) => (err ? reject(err) : resolve({ stdout: out }))
    )
  ));
} finally {
  server.close();
  await rm(profile, { recursive: true, force: true });
}

const match = stdout.match(/RESULT (\[.*?\])/);
const messages = match ? JSON.parse(match[1].replace(/&quot;/g, '"')) : null;
const of = (tag) => ({
  ran: (messages ?? []).filter((m) => m.startsWith(`${tag}:`)),
  fetched: [...new Set(requests.filter((u) => u.includes(`-${tag}`)))],
});
const sut = of("sut");
const control = of("control");
console.log(`browser: ${chrome}`);
console.log(`sandboxed frame: script ran ${JSON.stringify(sut.ran)}, requests ${JSON.stringify(sut.fetched)}`);
console.log(`control frame:   script ran ${JSON.stringify(control.ran)}, requests ${JSON.stringify(control.fetched)}`);

if (!messages || control.ran.length === 0 || control.fetched.length === 0) {
  console.error("INCONCLUSIVE: the control frame did not execute and fetch, so the harness proves nothing.");
  process.exit(2);
}
if (sut.ran.length > 0 || sut.fetched.length > 0) {
  console.error("FAIL: untrusted HTML executed script or made network requests.");
  process.exit(1);
}
console.log("PASS: no script ran and no request was made from the sandboxed frame.");
