import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the root to this app so lockfiles outside frontend/ cannot change it. When the
  // inferred root changes, Turbopack's .next/dev cache goes stale and the Tailwind
  // PostCSS step respawns workers forever, so page requests never finish.
  turbopack: {
    root: path.join(__dirname),
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
