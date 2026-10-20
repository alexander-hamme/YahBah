// Wraps HTML captured from a third-party site (e.g. a job board's confirmation
// page) in a standalone document for an <iframe sandbox srcDoc>. The sandbox
// (no allow-scripts, no allow-same-origin) stops script; this CSP stops every
// network load, so viewing the evidence never contacts the site. Links are
// inert: pointer-events off, and target=_blank, which the sandbox (no
// allow-popups) turns into a no-op if a click gets through anyway.
const CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'";

// Light "paper" look: captured pages are styled for light backgrounds, and it
// sets the foreign content visibly apart from the dashboard UI.
const STYLE = `
html, body { margin: 0; background: #eef0f4; color: #1d2130; }
body { padding: 10px 12px; font: 13px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; overflow-wrap: anywhere; }
a { color: #0e7490; pointer-events: none; }
h1 { font-size: 16px; } h2 { font-size: 15px; } h3, h4, h5, h6 { font-size: 14px; }
h1, h2, h3, h4, h5, h6, p { margin: 4px 0; }
img, svg, picture, video, audio { display: none !important; }
`;

export function untrustedHtmlSrcDoc(html: string): string {
  return (
    "<!doctype html><html><head>" +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    '<meta name="referrer" content="no-referrer">' +
    '<base target="_blank">' +
    `<style>${STYLE}</style>` +
    "</head><body>" +
    html +
    "</body></html>"
  );
}
