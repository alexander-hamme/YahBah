import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // useQuery comes from @/lib/queries: it renders as the server did while hydrating.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/lib/queries.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@tanstack/react-query",
              importNames: ["useQuery", "useSuspenseQuery", "useQueries"],
              message: "Use useQuery from @/lib/queries: it matches the server's render while hydrating.",
            },
          ],
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored, minified pdf.js worker.
    "public/pdf.worker.min.mjs",
  ]),
]);

export default eslintConfig;
