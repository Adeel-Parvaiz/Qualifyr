"use client"

import * as React from "react"
import Link from "next/link"
import { Menu, X } from "lucide-react"
import { QualifyrMark } from "@/components/qualifyr-mark"

import { Button } from "@/components/ui/button"
import { ThemeToggle } from "@/components/theme-toggle"
import { cn } from "@/lib/utils"

const sections = [
  { label: "Features", href: "#features" },
  { label: "How it works", href: "#how-it-works" },
  { label: "Sources", href: "#sources" },
  { label: "FAQ", href: "#faq" },
]

export function SiteNav() {
  const [scrolled, setScrolled] = React.useState(false)
  const [open, setOpen] = React.useState(false)

  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  return (
    <header
      className={cn(
        // Transparent over the hero, then a border and blur once it starts overlapping
        // content - otherwise the nav reads as a floating bar on a plain background.
        "sticky top-0 z-50 transition-colors duration-200",
        scrolled && "border-b bg-background/80 backdrop-blur-md"
      )}
    >
      {/* 3-column grid with equal 1fr side columns keeps the middle nav centered on the page
          regardless of how wide the logo or the action buttons are. */}
      <nav className="mx-auto grid h-16 max-w-6xl grid-cols-[1fr_auto_1fr] items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold justify-self-start">
          <span className="flex size-8 items-center justify-center rounded-lg bg-brand">
            <QualifyrMark className="size-5 text-brand-foreground" />
          </span>
          Qualifyr
        </Link>

        <div className="hidden items-center justify-center gap-1 md:flex">
          {sections.map((s) => (
            <a
              key={s.href}
              href={s.href}
              className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {s.label}
            </a>
          ))}
        </div>

        <div className="flex items-center justify-end gap-1">
          <ThemeToggle />
          {/* nativeButton={false} because these render as links: Base UI otherwise warns
              that it has lost native <button> semantics, which is the right complaint. */}
          <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/sign-in" />}>
            Sign in
          </Button>
          <Button
            size="sm"
            nativeButton={false}
            className="hidden bg-brand text-brand-foreground hover:bg-brand/90 sm:inline-flex"
            render={<Link href="/sign-up" />}
          >
            Get started free
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setOpen(!open)}
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
            <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
          </Button>
        </div>
      </nav>

      {open && (
        <div className="border-b bg-background px-4 pb-4 md:hidden">
          <div className="flex flex-col">
            {sections.map((s) => (
              <a
                key={s.href}
                href={s.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                {s.label}
              </a>
            ))}
          </div>
        </div>
      )}
    </header>
  )
}
