"use client"

import { Moon, Sun } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme-provider"

/** One toggle, shared by the dashboard shell and the marketing nav. */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme()
  return (
    <Button variant="ghost" size="icon" onClick={toggle} className={className}>
      {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
      <span className="sr-only">
        Switch to {theme === "dark" ? "light" : "dark"} theme
      </span>
    </Button>
  )
}
