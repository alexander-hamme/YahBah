"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface DeleteAllButtonProps {
  count: number;
  label?: string;
  onConfirm: () => Promise<void>;
}

export function DeleteAllButton({ count, label, onConfirm }: DeleteAllButtonProps) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleConfirm() {
    setLoading(true);
    try {
      await onConfirm();
    } finally {
      setLoading(false);
      setConfirming(false);
    }
  }

  if (confirming) {
    return (
      <span className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Delete {count} run{count !== 1 ? "s" : ""}?</span>
        <button
          onClick={handleConfirm}
          disabled={loading}
          className="text-red-400 hover:text-red-300 font-semibold disabled:opacity-50"
        >
          {loading ? "Deleting…" : "Yes, delete"}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={loading}
          className="text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      </span>
    );
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setConfirming(true)}
      className="text-red-400 hover:text-red-300 hover:bg-red-500/10 gap-1.5 h-7"
    >
      <Trash2 className="h-3.5 w-3.5" />
      {label ?? `Delete all (${count})`}
    </Button>
  );
}
