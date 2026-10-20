import { untrustedHtmlSrcDoc } from "@/lib/untrusted-html";

// The only sanctioned way to display HTML scraped from a job site. Never
// render such HTML with dangerouslySetInnerHTML: it would run in the
// dashboard's origin, which can change settings and trigger submissions.
export function UntrustedHtmlFrame({
  html,
  title,
  className,
}: {
  html: string;
  title: string;
  className?: string;
}) {
  return (
    <iframe
      sandbox=""
      srcDoc={untrustedHtmlSrcDoc(html)}
      referrerPolicy="no-referrer"
      title={title}
      className={className}
    />
  );
}
