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
          <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors group-focus-within/input:text-cyan-400" />
          <Input
            type="url"
            placeholder="Paste job URL to apply..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="pl-9 bg-white/5 border-white/10 text-foreground placeholder:text-muted-foreground transition-all focus:shadow-[0_0_0_3px_rgba(56,189,248,0.15)] focus:border-cyan-500/40"
            required
          />
        </div>
        <Button
          type="submit"
          disabled={mutation.isPending}
          className="bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-semibold px-5 shadow-[0_0_16px_rgba(56,189,248,0.2)] hover:shadow-[0_0_24px_rgba(56,189,248,0.35)] transition-all hover:scale-[1.03] active:scale-[0.97] border-0"
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
        <div className="flex items-center gap-2 text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2 animate-fade-in shadow-[0_0_12px_rgba(52,211,153,0.1)]">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          Job submitted successfully
        </div>
      )}

      {mutation.isError && (
        <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 animate-fade-in shadow-[0_0_12px_rgba(239,68,68,0.1)]">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {mutation.error.message}
        </div>
      )}
    </div>
  );
}
