import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { clearToasts } from "./src/lib/toast";
import { location, resetNavigation, router } from "./src/test/navigation";

// The app router isn't mounted in jsdom; components get a recording stand-in.
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => location.pathname,
  useSearchParams: () => new URLSearchParams(location.search),
}));

// Testing Library only auto-cleans when test globals are enabled; they aren't.
afterEach(() => {
  cleanup();
  clearToasts();
  vi.unstubAllGlobals();
  resetNavigation();
});

// jsdom lacks ResizeObserver (used by react-zoom-pan-pinch); layout is not
// under test, so a no-op is enough.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
