import Image from "next/image"
import { cn } from "@/lib/utils"

/** A single, reused treatment for every marketing illustration: a soft rounded panel that
 *  adapts to the theme (bg-muted, not a hardcoded dark slab), so the same monochrome art
 *  reads correctly in both light and dark mode. Used identically in five sections so the
 *  repetition reads as a deliberate design system rather than one-off decoration. */
export function Illustration({
  src,
  alt,
  width,
  height,
  className,
  priority,
  sizes = "(min-width: 1024px) 420px, 88vw",
}: {
  src: string
  alt: string
  width: number
  height: number
  className?: string
  priority?: boolean
  sizes?: string
}) {
  return (
    <div className={cn("overflow-hidden rounded-3xl border border-border/60 bg-muted/40", className)}>
      <Image
        src={src}
        alt={alt}
        width={width}
        height={height}
        priority={priority}
        sizes={sizes}
        className="h-full w-full object-cover"
      />
    </div>
  )
}
