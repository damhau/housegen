import { useEffect } from "react"
import { createFileRoute, Link, useRouter } from "@tanstack/react-router"
import { CircleAlert } from "lucide-react"
import { useMe } from "@/api/endpoints/accounts/accounts"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { ProviderButtons } from "@/components/accounts/ProviderButtons"
import { safeNext, signInOffered } from "@/lib/auth"

type SignInSearch = { next?: string; error?: "failed" | "cancelled" }

export const Route = createFileRoute("/signin")({
  validateSearch: (search: Record<string, unknown>): SignInSearch => ({
    next: typeof search.next === "string" ? search.next : undefined,
    error: search.error === "failed" || search.error === "cancelled" ? search.error : undefined,
  }),
  component: SignInPage,
})

const ERRORS: Record<NonNullable<SignInSearch["error"]>, string> = {
  failed: "Sign-in did not go through. Try again, or use your other account.",
  cancelled: "Sign-in was cancelled.",
}

/** Back to the page that sent the visitor here (any path of the app, so not a typed route). */
function Leave({ to }: { to: string }) {
  const router = useRouter()
  useEffect(() => router.history.replace(to), [router, to])
  return null
}

/** Where the SPA sends a signed-out visitor, and where a failed or cancelled sign-in lands. */
function SignInPage() {
  const { next, error } = Route.useSearch()
  const me = useMe()
  if (me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  // nothing to sign in to here (auth disabled, or no provider yet), or already signed in
  if (!me.data || !signInOffered(me.data) || me.data.user) return <Leave to={safeNext(next)} />
  return (
    <CenteredCard
      title="Sign in to housegen"
      description="Use your Google or Microsoft account. There is no separate sign-up: your first sign-in creates your account."
    >
      {error && (
        <p role="alert" className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          {ERRORS[error]}
        </p>
      )}
      <ProviderButtons providers={me.data.auth.providers} next={safeNext(next)} />
      <p className="text-xs text-muted-foreground">
        A coworker sent you an invitation link? Open it first: it signs you in and adds you to their organisation.
      </p>
      <p className="flex gap-3 text-xs text-muted-foreground">
        <Link to="/about" className="underline-offset-4 hover:underline">
          About housegen
        </Link>
        <Link to="/privacy" className="underline-offset-4 hover:underline">
          Privacy policy
        </Link>
      </p>
    </CenteredCard>
  )
}
