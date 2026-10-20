import { vi } from "vitest";

// Stand-in for next/navigation (mocked in vitest.setup.ts): the app router isn't mounted in jsdom.

export const router = {
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
  prefetch: vi.fn(),
};

export const location = { pathname: "/", search: "" };

export function setLocation(pathname: string, search = "") {
  location.pathname = pathname;
  location.search = search;
}

export function resetNavigation() {
  setLocation("/");
  Object.values(router).forEach((fn) => fn.mockReset());
}
