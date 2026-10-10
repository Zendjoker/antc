"use client";

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "zend.theme";

const Ctx = createContext<{ theme: ThemeChoice; setTheme: (t: ThemeChoice) => void } | null>(null);

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light";
  } catch {
    return "light";
  }
}

const listeners = new Set<() => void>();
const subscribe = (cb: () => void) => {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => { listeners.delete(cb); window.removeEventListener("storage", cb); };
};

function apply(theme: ThemeChoice) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useSyncExternalStore(subscribe, read, () => "light" as ThemeChoice);
  const setTheme = useCallback((t: ThemeChoice) => {
    try { localStorage.setItem(KEY, t); } catch { /* storage blocked: theme still applies for this session */ }
    apply(t);
    listeners.forEach((l) => l());
  }, []);
  useEffect(() => {
    apply(theme);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => theme === "system" && apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
  return <Ctx.Provider value={{ theme, setTheme }}>{children}</Ctx.Provider>;
}

export function useTheme() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme must be used inside ThemeProvider");
  return v;
}

/** Runs before paint to avoid a theme flash. */
export const themeInitScript = `try{var t=localStorage.getItem("${KEY}");var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}`;
