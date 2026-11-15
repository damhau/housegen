import type { QueryClient } from "@tanstack/react-query"
import type { MeOut } from "@/api/model"
import { getMeQueryKey } from "@/api/endpoints/accounts/accounts"
import { ApiError } from "@/api/http-client"

export const PROVIDER_LABEL: Record<string, string> = { google: "Google", microsoft: "Microsoft", local: "this server" }

const PUBLIC_PAGES = new Set(["/signin", "/about", "/privacy"])

/**
 * Pages anyone may open signed out: sign-in itself, the about and privacy pages (Google's OAuth
 * branding links to them), a share link (#24), an invitation link.
 */
export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PAGES.has(pathname) || pathname.startsWith("/s/") || pathname.startsWith("/invite/")
}

/** Whether this server asks people to sign in: a provider is configured and auth is not disabled. */
export function signInOffered(me: MeOut): boolean {
  return !me.auth.disabled && me.auth.providers.length > 0
}

/** A path on this site to come back to after sign-in (the backend checks it again). */
export function safeNext(next: string | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return "/"
  return next
}

/** The provider's sign-in: a full-page navigation through the backend, back to `next`. */
export function signInUrl(provider: string, next: string): string {
  return `/auth/login/${encodeURIComponent(provider)}?next=${encodeURIComponent(safeNext(next))}`
}

export const SIGN_OUT_URL = "/auth/logout"

/** An invitation link as the API returns it (relative) made into one to send. */
export function absoluteUrl(path: string): string {
  return `${window.location.origin}${path}`
}

/** The reason the API gives with a 403 or a 410 (`no_organisation`, `not_owner`, `accepted`, `expired`). */
export function errorReason(e: unknown): string | undefined {
  if (!(e instanceof ApiError)) return undefined
  const reason = (e.details as { reason?: unknown } | null | undefined)?.reason
  return typeof reason === "string" ? reason : undefined
}

/** After a change of who is signed in or which organisation they work in: everything shown may differ. */
export function refreshSession(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries()
}

/** A 401 from any call: the session ended, so ask again who is signed in (the gate takes it from there). */
export function onApiError(queryClient: QueryClient, error: unknown): void {
  if (error instanceof ApiError && error.status === 401) void queryClient.invalidateQueries({ queryKey: getMeQueryKey() })
}
