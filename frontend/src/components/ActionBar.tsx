import * as React from "react"
import { Ellipsis } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Menu, MenuItem, MenuSeparator, menuItemClass } from "@/components/ui/menu"
import { cn } from "@/lib/utils"

export type Action = {
  id: string
  label: string
  icon: React.ReactNode
  onSelect?: () => void
  /** a link (a download) instead of a click handler */
  href?: string
  /** "~35 min · ~$8": next to the label */
  hint?: string | null
  title?: string
  variant?: "default" | "outline" | "ghost"
  disabled?: boolean
  /** stays a button on a phone; the others move into the "More" menu there */
  primary?: boolean
  /** a square button showing only its icon; the label becomes its title */
  iconOnly?: boolean
  muted?: boolean
  destructive?: boolean
  /** the label in the menu, where there is no tooltip to explain a short one */
  menuLabel?: string
  /** the menu stays open after a click, so a label that changes ("Link copied") is seen */
  keepMenuOpen?: boolean
}

/**
 * A page's action buttons. They wrap when the row is too narrow; below the `sm` breakpoint only
 * the primary ones stay buttons and the rest are in a "More" menu, so a phone never scrolls sideways.
 */
export function ActionBar({ actions, status, className }: { actions: Action[]; status?: React.ReactNode; className?: string }) {
  const inMenu = actions.filter((a) => !a.primary)
  return (
    <div className={cn("flex flex-wrap items-center justify-end gap-2", className)}>
      {status}
      {actions.map((a) => {
        const variant = a.variant ?? "outline"
        const size = a.iconOnly ? "icon" : "sm"
        const extra = cn(!a.primary && "hidden sm:inline-flex", a.muted && "text-muted-foreground")
        const content = (
          <>
            {a.icon}
            {!a.iconOnly && a.label}
            {a.hint && <span className={cn("font-normal", a.variant === "default" ? "opacity-80" : "text-muted-foreground")}>{a.hint}</span>}
          </>
        )
        const title = a.title ?? (a.iconOnly ? a.label : undefined)
        return a.href ? (
          <a key={a.id} href={a.href} className={cn(buttonVariants({ variant, size }), extra)} title={title} aria-label={a.iconOnly ? a.label : undefined}>
            {content}
          </a>
        ) : (
          <Button key={a.id} variant={variant} size={size} className={extra} onClick={a.onSelect} disabled={a.disabled} title={title} aria-label={a.iconOnly ? a.label : undefined}>
            {content}
          </Button>
        )
      })}
      {inMenu.length > 0 && (
        <div className="sm:hidden">
          <Menu label="More actions" className="h-8 border border-input px-2 shadow-sm" button={<Ellipsis className="size-4" />}>
            {(close) =>
              inMenu.map((a, i) => (
                <React.Fragment key={a.id}>
                  {a.destructive && i > 0 && <MenuSeparator />}
                  {a.href ? (
                    <a href={a.href} role="menuitem" className={menuItemClass} onClick={close}>
                      {a.icon}
                      {a.menuLabel ?? a.label}
                    </a>
                  ) : (
                    <MenuItem
                      disabled={a.disabled}
                      className={cn(a.destructive && "text-destructive hover:text-destructive")}
                      onClick={() => {
                        if (!a.keepMenuOpen) close()
                        a.onSelect?.()
                      }}
                    >
                      {a.icon}
                      <span className="min-w-0 flex-1">{a.menuLabel ?? a.label}</span>
                      {a.hint && <span className="shrink-0 text-xs text-muted-foreground">{a.hint}</span>}
                    </MenuItem>
                  )}
                </React.Fragment>
              ))
            }
          </Menu>
        </div>
      )}
    </div>
  )
}
