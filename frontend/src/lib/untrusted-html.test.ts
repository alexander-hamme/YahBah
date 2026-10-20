import { describe, expect, it } from "vitest";
import { untrustedHtmlSrcDoc } from "./untrusted-html";

// Structure only: jsdom applies neither iframe sandboxing nor CSP, so the
// behaviour (no script, no network) is checked in a real browser by
// scripts/check-untrusted-html-sandbox.mjs.

const PAYLOAD =
  '<h1>Thanks</h1><img src="https://tracker.example/p.png" onerror="window.__x=1"><script>window.__x=2</script>';

function cspDirectives(content: string): Map<string, string> {
  return new Map(
    content
      .split(";")
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => {
        const [name, ...values] = d.split(/\s+/);
        return [name, values.join(" ")] as const;
      })
  );
}

describe("untrustedHtmlSrcDoc", () => {
  it("puts the CSP meta before the payload", () => {
    const srcdoc = untrustedHtmlSrcDoc(PAYLOAD);
    const csp = srcdoc.indexOf('http-equiv="Content-Security-Policy"');
    expect(csp).toBeGreaterThan(-1);
    expect(csp).toBeLessThan(srcdoc.indexOf("<body>"));
    expect(csp).toBeLessThan(srcdoc.indexOf(PAYLOAD));
  });

  it("makes the CSP the first element of <head> when parsed", () => {
    const doc = new DOMParser().parseFromString(untrustedHtmlSrcDoc(PAYLOAD), "text/html");
    const first = doc.head.firstElementChild;
    expect(first?.getAttribute("http-equiv")).toBe("Content-Security-Policy");
    // The payload lands in <body>, after the policy.
    expect(doc.body.querySelector("img")).not.toBeNull();
    expect(doc.head.querySelector("img, script")).toBeNull();
  });

  it("blocks every network load and allows no script", () => {
    const doc = new DOMParser().parseFromString(untrustedHtmlSrcDoc(PAYLOAD), "text/html");
    const content = doc.head.firstElementChild?.getAttribute("content") ?? "";
    const csp = cspDirectives(content);
    expect(csp.get("default-src")).toBe("'none'");
    expect(csp.get("img-src")).toBe("data:");
    expect(csp.get("style-src")).toBe("'unsafe-inline'");
    expect(csp.get("form-action")).toBe("'none'");
    expect(csp.get("base-uri")).toBe("'none'");
    // script-src must fall back to default-src 'none'.
    expect(csp.has("script-src")).toBe(false);
    expect(content).not.toMatch(/https?:|\*/);
  });

  it("keeps its own policy first even if the payload tries to add one", () => {
    const hostile =
      '</body></html><head><meta http-equiv="Content-Security-Policy" content="default-src *"></head>';
    const doc = new DOMParser().parseFromString(untrustedHtmlSrcDoc(hostile), "text/html");
    const first = doc.querySelector('meta[http-equiv="Content-Security-Policy"]');
    expect(first?.getAttribute("content")).toMatch(/^default-src 'none'/);
    expect(first?.parentElement).toBe(doc.head);
  });
});
