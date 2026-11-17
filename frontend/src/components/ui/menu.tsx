import * as React from "react"
import { cn } from "@/lib/utils"

/** A button that opens a small panel under it (or above it: `side="top"`, in a bottom toolbar);
 * a click outside or Escape closes it. */
export function Menu({
  button,
  label,
  align = "end",
  side = "bottom",
  className,
  children,
}: {
  button: React.ReactNode
  label: string
  align?: "start" | "end"
  side?: "top" | "bottom"
  className?: string
  children: (close: () => void) => React.ReactNode
}) {
  const [open, setOpen] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("pointerdown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("pointerdown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          open && "bg-accent",
          className,
        )}
      >
        {button}
      </button>
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-50 min-w-60 rounded-lg border bg-popover p-1 text-popover-foreground shadow-md",
            side === "top" ? "bottom-full mb-1" : "top-full mt-1",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

export const menuItemClass =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0"

export function MenuItem({ className, type = "button", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} role="menuitem" className={cn(menuItemClass, className)} {...props} />
}

export function MenuLabel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-2 py-1.5 text-xs font-medium text-muted-foreground", className)} {...props} />
}

export function MenuSeparator() {
  return <div role="separator" className="-mx-1 my-1 h-px bg-border" />
}
