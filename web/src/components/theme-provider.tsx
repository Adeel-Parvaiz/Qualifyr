"use client"

import * as React from "react"

/** Theme lives above the route groups.
 *
 * It used to be a `useState(false)` inside AppShell, which meant two things: it reset on
 * every reload, and a page outside the shell - the landing page, sign-in - had no way to
 * reach it. Both are fixed by hoisting it here and persisting the choice.
 */

type Theme = "light" | "dark"

const STORAGE_KEY = "qualifyr-theme"

const ThemeContext = React.createContext<{
  theme: Theme
  setTheme: (theme: Theme) => void
  toggle: () => void
} | null>(null)

/** Runs before paint, so the first frame is already the right colour.
 *
 * Without this the browser renders light, React hydrates, and the page snaps to dark - a
 * flash on every single load for anyone using dark mode. It has to be inline and
 * synchronous in <head>; a useEffect is far too late.
 */
export const themeScript = `
(function () {
  try {
    var stored = localStorage.getItem(${JSON.stringify(STORAGE_KEY)});
    var dark = stored
      ? stored === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {}
})();
`

/** The `dark` class on <html> is the source of truth, so read it rather than mirror it.
 *
 * The inline script sets that class before React exists, so component state can only ever
 * be a copy that starts out wrong. useSyncExternalStore subscribes to the real thing:
 * the server snapshot is "light" (matching the server-rendered markup, so hydration is
 * clean) and the client snapshot reads the DOM.
 */
function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
  return () => observer.disconnect()
}

const getSnapshot = (): Theme =>
  document.documentElement.classList.contains("dark") ? "dark" : "light"

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = React.useSyncExternalStore(subscribe, getSnapshot, () => "light" as Theme)

  const setTheme = React.useCallback((next: Theme) => {
    // Only touch the DOM; the store above notices and re-renders whatever is subscribed.
    document.documentElement.classList.toggle("dark", next === "dark")
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Private mode or blocked storage: the theme still applies for this page view.
    }
  }, [])

  const value = React.useMemo(
    () => ({ theme, setTheme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") }),
    [theme, setTheme]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const ctx = React.useContext(ThemeContext)
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>")
  return ctx
}
