import { useState } from "react"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Building2 } from "lucide-react"
import { useAcceptInvitation, useGetInvitation, useMe } from "@/api/endpoints/accounts/accounts"
import type { InvitationPreviewOut, MeOut } from "@/api/model"
import { ApiError } from "@/api/http-client"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { ProviderButtons } from "@/components/accounts/ProviderButtons"
import { Button } from "@/components/ui/button"
import { errorReason, refreshSession, signInOffered } from "@/lib/auth"
import { errorMessage, formatDay } from "@/lib/utils"

export const Route = createFileRoute("/invite/$token")({ component: InvitePage })

const GONE: Record<string, string> = {
  accepted: "This invitation was already used.",
  expired: "This invitation has expired.",
}

/**
 * An invitation link (#80), open signed out: what it is for, then sign in and come back here, or
 * join with the account signed in now. The link is what lets someone in, whatever account opens it.
 */
function InvitePage() {
  const { token } = Route.useParams()
  const preview = useGetInvitation(token, { query: { retry: false } })
  const me = useMe()
  if (preview.isPending || me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  if (preview.error || !preview.data) {
    const missing = preview.error instanceof ApiError && preview.error.status === 404
    return (
      <CenteredCard
        title="This invitation link is not valid"
        description={
          missing ? "It may have been revoked, or the link was cut short when it was copied." : errorMessage(preview.error)
        }
      >
        <HomeLink />
      </CenteredCard>
    )
  }
  const invitation = preview.data
  if (invitation.status !== "pending")
    return (
      <CenteredCard
        title={GONE[invitation.status]}
        description={
          invitation.status === "accepted"
            ? `If you accepted it yourself, you are already in ${invitation.organisation_name}. Otherwise ask ${invitation.invited_by ?? "whoever sent it"} for a new link.`
            : `Ask ${invitation.invited_by ?? "whoever sent it"} for a new link to ${invitation.organisation_name}.`
        }
      >
        <HomeLink />
      </CenteredCard>
    )
  return <PendingInvitation token={token} invitation={invitation} me={me.data} />
}

function HomeLink() {
  return (
    <Link to="/" className="text-sm underline underline-offset-4">
      Go to housegen
    </Link>
  )
}

function PendingInvitation({
  token,
  invitation,
  me,
}: {
  token: string
  invitation: InvitationPreviewOut
  me: MeOut | undefined
}) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const accept = useAcceptInvitation()
  const [error, setError] = useState<string | null>(null)
  const user = me?.user ?? null
  const here = `/invite/${token}`

  async function join() {
    setError(null)
    try {
      await accept.mutateAsync({ token })
      await refreshSession(qc)
      await navigate({ to: "/" })
    } catch (e) {
      const reason = errorReason(e)
      setError(reason && GONE[reason] ? GONE[reason] : errorMessage(e))
    }
  }

  const otherEmail = user?.email && user.email.toLowerCase() !== invitation.email.toLowerCase()
  return (
    <CenteredCard
      aside={
        <span className="mb-2 grid size-10 place-items-center rounded-lg bg-accent text-accent-foreground">
          <Building2 className="size-5" />
        </span>
      }
      title={`Join ${invitation.organisation_name}`}
      description={
        <>
          {invitation.invited_by ?? "Someone"} invited <strong>{invitation.email}</strong> to work on the organisation's projects
          in housegen. The link works until {formatDay(invitation.expires_at)}.
        </>
      }
    >
      {user ? (
        <>
          <p className="text-sm">
            You are signed in as <strong>{user.email ?? user.name}</strong>.
            {otherEmail && " The link works with any account: make sure this is the one you want to join with."}
          </p>
          <Button size="lg" onClick={() => void join()} disabled={accept.isPending}>
            {accept.isPending ? "Joining…" : `Join ${invitation.organisation_name}`}
          </Button>
          {error && <p className="text-sm text-destructive">{error}</p>}
          {me && signInOffered(me) && (
            <details className="text-sm text-muted-foreground">
              <summary className="cursor-pointer">Use another account</summary>
              <ProviderButtons providers={me.auth.providers} next={here} className="mt-2" />
            </details>
          )}
        </>
      ) : me && signInOffered(me) ? (
        <>
          <p className="text-sm text-muted-foreground">Sign in to accept. You come back here afterwards.</p>
          <ProviderButtons providers={me.auth.providers} next={here} />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          Sign-in is not set up on this server, so the invitation cannot be accepted here.
        </p>
      )}
    </CenteredCard>
  )
}
