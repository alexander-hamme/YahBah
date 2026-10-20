import { ApiError } from "./api";

// A tiny module-level toast store, so non-React code (the react-query
// MutationCache) can raise toasts too. <Toaster> renders it.

export type ToastKind = "success" | "error" | "info";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  body?: string;
  error?: ApiError;
}

const DURATION_MS: Record<ToastKind, number> = { success: 5_000, info: 7_000, error: 12_000 };

let items: readonly ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function push(item: Omit<ToastItem, "id">): number {
  const id = nextId++;
  items = [...items, { ...item, id }].slice(-4);
  emit();
  setTimeout(() => dismissToast(id), DURATION_MS[item.kind]);
  return id;
}

export function dismissToast(id: number) {
  if (!items.some((t) => t.id === id)) return;
  items = items.filter((t) => t.id !== id);
  emit();
}

/** Drop every toast (between tests). */
export function clearToasts() {
  items = [];
  emit();
}

export function subscribeToasts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getToasts(): readonly ToastItem[] {
  return items;
}

export function asApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  const message = err instanceof Error ? err.message : String(err);
  return new ApiError(0, { code: "client_error", message });
}

export const toast = {
  success: (title: string, body?: string) => push({ kind: "success", title, body }),
  info: (title: string, body?: string) => push({ kind: "info", title, body }),
  error: (err: unknown, title = "That didn't work") =>
    push({ kind: "error", title, error: asApiError(err) }),
};
