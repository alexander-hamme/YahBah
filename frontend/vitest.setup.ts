import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-cleans when test globals are enabled; they aren't.
afterEach(cleanup);

// jsdom lacks ResizeObserver (used by react-zoom-pan-pinch); layout is not
// under test, so a no-op is enough.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
