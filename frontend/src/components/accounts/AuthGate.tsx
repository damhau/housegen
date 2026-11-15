import type { ReactNode } from "react"
import { Navigate, useRouterState } from "@tanstack/react-router"
import { useMe } from "@/api/endpoints/accounts/accounts"
import { isPublicPath, signInOffered } from "@/lib/auth"

/**
 * Sends a signed-out visitor to the sign-in page when this server offers sign-in, and a signed-in
 * user without an organisation to the first-login wizard. Share and invitation links stay open
 * signed out; with auth disabled, no provider, or /me unreachable, the app shows as before.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  // the page on screen, not the one being navigated to: while a route's code loads, the Outlet
  // still shows the previous page, which must not render (and fetch) once the gate let it through
  const location = useRouterState({ select: (s) => s.resolvedLocation ?? s.location })
  const me = useMe()
  if (isPublicPath(location.pathname)) return children
  if (me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  if (!me.data) return children
  if (!me.data.user) {
    if (!signInOffered(me.data)) return children
    return <Navigate to="/signin" search={{ next: location.href }} replace />
  }
  if (!me.data.organisation && location.pathname !== "/welcome") return <Navigate to="/welcome" replace />
  return children
}
