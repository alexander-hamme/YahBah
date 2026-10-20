"use client";

import { createContext, useCallback, useContext, useSyncExternalStore } from "react";

interface DevModeContextValue {
  devMode: boolean;
  toggleDevMode: () => void;
}

const DevModeContext = createContext<DevModeContextValue>({
  devMode: false,
  toggleDevMode: () => {},
});

const STORAGE_KEY = "yahbah-dev-mode";
const CHANGE_EVENT = "yahbah-dev-mode-change";

// localStorage is the store; "storage" covers other tabs, CHANGE_EVENT this one.
function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

function readDevMode(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false; // storage blocked: dev mode is a convenience, default off
  }
}

export function DevModeProvider({ children }: { children: React.ReactNode }) {
  const devMode = useSyncExternalStore(subscribe, readDevMode, () => false);

  const toggleDevMode = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(!readDevMode()));
    } catch {
      return;
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return (
    <DevModeContext.Provider value={{ devMode, toggleDevMode }}>
      {children}
    </DevModeContext.Provider>
  );
}

export function useDevMode() {
  return useContext(DevModeContext);
}
