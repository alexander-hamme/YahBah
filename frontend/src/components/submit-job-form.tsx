"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Send, CheckCircle2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { submitJob } from "@/lib/api";

export function SubmitJobForm() {
  const [url, setUrl] = useState("");
  const [showSuccess, setShowSuccess] = useState(false);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (jobUrl: string) => submitJob(jobUrl),
    onSuccess: () => {
      setUrl("");
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 3000);
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    mutation.mutate(trimmed);
  }

  return (
    <div className="space-y-2">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="relative flex-1 group/input">
          <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors group-focus-within/input:text-primary" />
          <Input
            type="url"
            placeholder="Paste job URL to apply..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="pl-9 transition-shadow focus:shadow-[0_0_0_3px_oklch(0.55_0.22_270_/_0.15)] focus:border-primary"
            required
          />
        </div>
        <Button
          type="submit"
          disabled={mutation.isPending}
          className="bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-5 shadow-sm transition-all hover:scale-[1.03] hover:shadow-md active:scale-[0.97]"
        >
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <>
              <Send className="h-4 w-4 mr-1.5" />
              Submit
            </>
          )}
        </Button>
      </form>

      {showSuccess && (
        <div className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg px-3 py-2 animate-fade-in">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          Job submitted successfully
        </div>
      )}

      {mutation.isError && (
        <div className="flex items-center gap-2 text-sm text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 animate-fade-in">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {mutation.error.message}
        </div>
      )}
    </div>
  );
}
