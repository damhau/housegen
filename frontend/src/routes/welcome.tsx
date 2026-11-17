import { useState } from "react"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { ArrowRight, Building2, Mail } from "lucide-react"
import { useAcceptInvitation, useCreateOrganisation, useMe, useMyInvitations } from "@/api/endpoints/accounts/accounts"
import type { InvitationOut, MeOut, OrganisationOut } from "@/api/model"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { CopyLinkButton } from "@/components/accounts/CopyLinkButton"
import { InviteForm } from "@/components/accounts/InviteForm"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PROVIDER_LABEL, refreshSession, SIGN_OUT_URL } from "@/lib/auth"
import { cn, errorMessage, formatDay } from "@/lib/utils"

export const Route = createFileRoute("/welcome")({ component: WelcomePage })

/**
 * The first-login wizard (#80): join an organisation that invited you, or create one and become
 * its owner, then (optional) invite coworkers. The header's organisation menu opens it again to
 * create or join another.
 */
function WelcomePage() {
  const me = useMe()
  const [created, setCreated] = useState<OrganisationOut | null>(null)
  if (me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  if (!me.data?.user)
    return (
      <CenteredCard title="Organisations need sign-in" description="Sign-in is not set up on this server.">
        <Link to="/" className="text-sm underline underline-offset-4">
          Back to projects
        </Link>
      </CenteredCard>
    )
  if (created) return <InviteStep organisation={created} emailed={me.data.auth.invitation_email ?? false} />
  return <ChooseStep me={me.data} onCreated={setCreated} />
}

function Steps({ step }: { step: 1 | 2 }) {
  const steps = ["Organisation", "Invite coworkers"]
  return (
    <ol className="mb-2 flex items-center gap-2 text-xs">
      {steps.map((label, i) => (
        <li key={label} className="flex items-center gap-2">
          {i > 0 && <span className="h-px w-6 bg-border" />}
          <span
            className={cn(
              "grid size-5 place-items-center rounded-full border text-[11px] font-semibold",
              i + 1 === step ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground",
            )}
          >
            {i + 1}
          </span>
          <span className={i + 1 === step ? "font-medium" : "text-muted-foreground"}>{label}</span>
        </li>
      ))}
    </ol>
  )
}

function ChooseStep({ me, onCreated }: { me: MeOut; onCreated: (o: OrganisationOut) => void }) {
  const user = me.user!
  const first = me.organisations.length === 0
  const qc = useQueryClient()
  const navigate = useNavigate()
  const invitations = useMyInvitations()
  const accept = useAcceptInvitation()
  const create = useCreateOrganisation()
  const [name, setName] = useState("")
  const [joining, setJoining] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function join(token: string) {
    setError(null)
    setJoining(token)
    try {
      await accept.mutateAsync({ token })
      await refreshSession(qc)
      await navigate({ to: "/" })
    } catch (e) {
      setError(errorMessage(e))
      void invitations.refetch()
    } finally {
      setJoining(null)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError(null)
    try {
      const organisation = await create.mutateAsync({ data: { name: name.trim() } })
      await refreshSession(qc)
      onCreated(organisation)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const firstName = user.name.split(/\s+/)[0]
  const list = invitations.data ?? []
  return (
    <CenteredCard
      aside={<Steps step={1} />}
      title={first ? `Welcome${firstName ? `, ${firstName}` : ""}` : "Join or create an organisation"}
      description="Projects belong to an organisation, and everyone in it shares them. Join one that invited you, or create your own."
    >
      <section className="grid gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-medium">
          <Mail className="size-4 text-muted-foreground" />
          Invitations{user.email ? ` for ${user.email}` : ""}
        </h2>
        {invitations.isPending && <p className="text-sm text-muted-foreground">Looking for invitations…</p>}
        {list.map((i) => (
          <div key={i.id} className="flex items-center gap-3 rounded-lg border p-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-md bg-accent text-accent-foreground">
              <Building2 className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{i.organisation_name}</div>
              <div className="text-xs text-muted-foreground">
                {i.invited_by ? `${i.invited_by} invited you` : "You are invited"} · until {formatDay(i.expires_at)}
              </div>
            </div>
            <Button size="sm" disabled={joining !== null} onClick={() => void join(i.token)}>
              {joining === i.token ? "Joining…" : "Join"}
            </Button>
          </div>
        ))}
        {invitations.isSuccess && list.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {user.email_verified
              ? "No invitation for this address. If a coworker sent you a link, open it to join their organisation."
              : `${PROVIDER_LABEL[user.provider] ?? "Your provider"} does not confirm ${user.email ?? "your email"}, so invitations sent to it cannot be listed here. Open the link a coworker sent you to join their organisation.`}
          </p>
        )}
      </section>

      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>

      <form onSubmit={(e) => void submit(e)} className="grid gap-2">
        <label htmlFor="organisation-name" className="text-sm font-medium">
          Create an organisation
        </label>
        <p className="text-xs text-muted-foreground">You become its owner and can invite your coworkers next.</p>
        <div className="flex gap-2">
          <Input
            id="organisation-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your agency or team"
            maxLength={200}
          />
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </div>
      </form>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <p className="border-t pt-3 text-xs text-muted-foreground">
        {first ? (
          <>
            Signed in as {user.email ?? user.name}.{" "}
            <a href={SIGN_OUT_URL} className="underline underline-offset-4">
              Use another account
            </a>
          </>
        ) : (
          <Link to="/" className="underline underline-offset-4">
            Back to projects
          </Link>
        )}
      </p>
    </CenteredCard>
  )
}

function InviteStep({ organisation, emailed }: { organisation: OrganisationOut; emailed: boolean }) {
  const navigate = useNavigate()
  const [links, setLinks] = useState<InvitationOut[]>([])
  const add = (made: InvitationOut[]) => setLinks((prev) => [...prev, ...made.filter((m) => !prev.some((p) => p.id === m.id))])
  return (
    <CenteredCard
      aside={<Steps step={2} />}
      title="Invite coworkers"
      description={
        <>
          Optional. Each address gets its own link to <strong>{organisation.name}</strong>
          {emailed
            ? ", by email from housegen with you as the reply address."
            : ": copy it and send it yourself, housegen sends no email."}{" "}
          You can invite people later from the organisation settings.
        </>
      }
    >
      <InviteForm organisationId={organisation.id} onCreated={add} />
      {links.length > 0 && (
        <ul className="grid gap-2">
          {links.map((l) => (
            <li key={l.id} className="flex items-center gap-2 rounded-lg border px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{l.email}</div>
                {l.email_sent?.sent === false && (
                  <div className="text-xs text-destructive" title={l.email_sent.error ?? undefined}>
                    Couldn't send the email: copy the link.
                  </div>
                )}
                {l.email_sent?.sent && <div className="text-xs text-muted-foreground">emailed</div>}
              </div>
              <CopyLinkButton url={l.url} />
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-end border-t pt-3">
        <Button variant={links.length > 0 ? "default" : "ghost"} onClick={() => void navigate({ to: "/" })}>
          {links.length > 0 ? "Go to projects" : "Skip for now"}
          <ArrowRight />
        </Button>
      </div>
    </CenteredCard>
  )
}
