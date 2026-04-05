"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { getRuntimeSettings, setTestingMode } from "@/lib/api";

export function TestingToggle() {
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["runtime-settings"],
    queryFn: getRuntimeSettings,
    refetchInterval: 10_000,
  });

  const mutation = useMutation({
    mutationFn: (enabled: boolean) => setTestingMode(enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runtime-settings"] });
    },
  });

  const testing = data?.testing_mode ?? false;

  return (
    <button
      onClick={() => mutation.mutate(!testing)}
      disabled={mutation.isPending}
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border transition-all ${
        testing
          ? "text-amber-400 bg-amber-500/15 border-amber-500/30 shadow-[0_0_10px_rgba(251,191,36,0.15)]"
          : "text-muted-foreground bg-white/5 border-white/10 hover:text-foreground hover:bg-white/10"
      }`}
      title={testing ? "Testing mode ON — submissions will be skipped" : "Testing mode OFF — submissions are live"}
    >
      <FlaskConical className="h-3.5 w-3.5" />
      {testing ? "TESTING" : "LIVE"}
    </button>
  );
}
