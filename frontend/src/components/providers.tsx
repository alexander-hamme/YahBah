"use client";

import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { DevModeProvider } from "@/components/dev-mode-context";
import { Toaster } from "@/components/toaster";
import { toast } from "@/lib/toast";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      /** The component renders this mutation's errors itself: no toast. */
      inlineErrors?: boolean;
      /** Toast title on failure, e.g. "Couldn't cancel the run". */
      errorTitle?: string;
    };
  }
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    // One error surface: every failed mutation toasts its §8 error, unless the
    // component shows it inline.
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.inlineErrors) return;
        toast.error(error, mutation.meta?.errorTitle);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <DevModeProvider>
        {children}
        <Toaster />
      </DevModeProvider>
    </QueryClientProvider>
  );
}
