import { useState } from "react"
import { cn } from "@/lib/utils"

function initials(name: string, email?: string | null): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  const first = words[0] ?? ""
  const last = words.length > 1 ? (words[words.length - 1] ?? "") : ""
  const letters = last ? first.charAt(0) + last.charAt(0) : first.slice(0, 2)
  return (letters || (email ?? "?").charAt(0)).toUpperCase()
}

/** The provider's picture, or initials when there is none (Microsoft sends none) or it fails to load. */
export function Avatar({
  name,
  email,
  picture,
  className,
}: {
  name: string
  email?: string | null
  picture?: string | null
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const base = cn("size-7 shrink-0 rounded-full", className)
  if (picture && !failed)
    // Google's picture URLs refuse requests that carry a referrer from another site
    return (
      <img
        src={picture}
        alt=""
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        className={cn(base, "object-cover")}
      />
    )
  return (
    <span aria-hidden className={cn(base, "grid place-items-center bg-accent text-[11px] font-semibold text-accent-foreground")}>
      {initials(name, email)}
    </span>
  )
}
