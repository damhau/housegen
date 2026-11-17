import { useState } from "react"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { DoorOpen, Mail, Trash2, UserMinus } from "lucide-react"
import {
  getListInvitationsQueryKey,
  getListMembersQueryKey,
  getMeQueryKey,
  useListInvitations,
  useListMembers,
  useMe,
  useOrganisationLimits,
  useRemoveMember,
  useRenameOrganisation,
  useRevokeInvitation,
  useSendInvitation,
} from "@/api/endpoints/accounts/accounts"
import type { InvitationOut, MeOut, OrganisationOut } from "@/api/model"
import { ApiError } from "@/api/http-client"
import { Avatar } from "@/components/accounts/Avatar"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { CopyLinkButton } from "@/components/accounts/CopyLinkButton"
import { InviteForm } from "@/components/accounts/InviteForm"
import { LimitsList } from "@/components/accounts/Limits"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { refreshSession } from "@/lib/auth"
import { amount } from "@/lib/limits"
import { errorMessage, formatDate, formatDay, relTime } from "@/lib/utils"

export const Route = createFileRoute("/organisation")({ component: OrganisationPage })

/** The settings of the organisation the session works in: name, members, pending invitations, its limits. */
function OrganisationPage() {
  const me = useMe()
  if (me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  const organisation = me.data?.organisation
  if (!me.data?.user || !organisation)
    return (
      <CenteredCard title="No organisation" description="Sign in and create or join an organisation first.">
        <Link to="/" className="text-sm underline underline-offset-4">
          Back to projects
        </Link>
      </CenteredCard>
    )
  return <OrganisationSettings key={organisation.id} me={me.data} organisation={organisation} />
}

function OrganisationSettings({ me, organisation }: { me: MeOut; organisation: OrganisationOut }) {
  const owner = organisation.role === "owner"
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold tracking-tight">{organisation.name}</h1>
          <Badge variant={owner ? "default" : "secondary"}>{organisation.role}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Everyone in the organisation sees and edits all of its projects.
          {!owner && " Only an owner can rename it, invite people or remove members."}
        </p>
      </div>
      {owner && <RenameCard organisation={organisation} />}
      <MembersCard me={me} organisation={organisation} />
      {owner && <InvitationsCard organisation={organisation} emailed={me.auth.invitation_email ?? false} />}
      <LimitsCard organisation={organisation} />
    </div>
  )
}

function RenameCard({ organisation }: { organisation: OrganisationOut }) {
  const qc = useQueryClient()
  const rename = useRenameOrganisation()
  const [name, setName] = useState(organisation.name)
  const [error, setError] = useState<string | null>(null)
  const changed = name.trim() !== "" && name.trim() !== organisation.name

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await rename.mutateAsync({ organisationId: organisation.id, data: { name: name.trim() } })
      await qc.invalidateQueries({ queryKey: getMeQueryKey() })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Name</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void submit(e)} className="flex gap-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            aria-label="Organisation name"
            className="min-w-0"
          />
          <Button type="submit" variant="outline" disabled={!changed || rename.isPending}>
            {rename.isPending ? "Saving…" : "Rename"}
          </Button>
        </form>
        {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  )
}

function MembersCard({ me, organisation }: { me: MeOut; organisation: OrganisationOut }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const members = useListMembers(organisation.id)
  const remove = useRemoveMember()
  const [error, setError] = useState<string | null>(null)
  const owner = organisation.role === "owner"
  const myId = me.user?.id

  async function onRemove(userId: string, name: string) {
    const leaving = userId === myId
    const question = leaving
      ? `Leave ${organisation.name}? You lose access to its projects until someone invites you again.`
      : `Remove ${name} from ${organisation.name}? They lose access to its projects.`
    if (!confirm(question)) return
    setError(null)
    try {
      await remove.mutateAsync({ organisationId: organisation.id, userId })
      if (leaving) {
        await navigate({ to: "/" })
        await refreshSession(qc)
      } else {
        await qc.invalidateQueries({ queryKey: getListMembersQueryKey(organisation.id) })
      }
    } catch (e) {
      // 409: the last owner cannot go, and there is no way yet to make someone else an owner
      setError(
        e instanceof ApiError && e.status === 409
          ? "You are its only owner: an organisation cannot be left without one."
          : errorMessage(e),
      )
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Members</CardTitle>
        {members.data && (
          <CardDescription>
            {members.data.length} {members.data.length === 1 ? "person" : "people"}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {members.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {members.error ? <p className="text-sm text-destructive">{errorMessage(members.error)}</p> : null}
        {members.data?.map((m) => {
          const self = m.user_id === myId
          return (
            <div key={m.user_id} className="flex items-center gap-3 rounded-md px-1 py-2">
              <Avatar name={m.name} email={m.email} picture={m.picture} className="size-8" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {m.name || m.email}
                  {self && <span className="font-normal text-muted-foreground"> (you)</span>}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {m.email ? `${m.email} · ` : ""}joined {formatDay(m.joined_at)}
                </div>
              </div>
              <Badge variant={m.role === "owner" ? "default" : "secondary"}>{m.role}</Badge>
              <div className="flex w-24 justify-end">
                {self ? (
                  <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={() => void onRemove(m.user_id, m.name)}>
                    <DoorOpen />
                    Leave
                  </Button>
                ) : owner ? (
                  <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={() => void onRemove(m.user_id, m.name)}>
                    <UserMinus />
                    Remove
                  </Button>
                ) : null}
              </div>
            </div>
          )
        })}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  )
}

function LimitsCard({ organisation }: { organisation: OrganisationOut }) {
  const limits = useOrganisationLimits(organisation.id)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Limits</CardTitle>
        <CardDescription>
          The administrator of this server sets them. A job over a limit does not start: the message says which limit and when
          it resets.
          {limits.data &&
            ` A listing photo set needs about ${amount(limits.data.listing_estimate_usd, true)} left in the month's budget.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {limits.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {limits.error ? <p className="text-sm text-destructive">{errorMessage(limits.error)}</p> : null}
        {limits.data && <LimitsList limits={limits.data} />}
      </CardContent>
    </Card>
  )
}

/** What happened to an invitation's email: sent (when), or why not, or none (email off). */
function EmailStatus({ invitation, failed }: { invitation: InvitationOut; failed: string | undefined }) {
  // the last try failed: known from this page's own send (with Resend's reason), or from the times
  const lastFailed =
    failed !== undefined || (invitation.email_attempted_at != null && invitation.email_attempted_at !== invitation.emailed_at)
  if (lastFailed)
    return (
      <div className="text-xs text-destructive" title={failed || undefined}>
        Couldn't send the email: copy the link.{failed ? ` (${failed})` : ""}
      </div>
    )
  if (!invitation.emailed_at) return null
  return (
    <div className="text-xs text-muted-foreground" title={formatDate(invitation.emailed_at)}>
      emailed {relTime(invitation.emailed_at)}
    </div>
  )
}

function InvitationsCard({ organisation, emailed }: { organisation: OrganisationOut; emailed: boolean }) {
  const qc = useQueryClient()
  const invitations = useListInvitations(organisation.id)
  const revoke = useRevokeInvitation()
  const send = useSendInvitation()
  const [fresh, setFresh] = useState<string[]>([])
  // the invitations whose last email did not go out, with Resend's reason
  const [failed, setFailed] = useState<Record<string, string>>({})
  // why "Send again" did not send (too soon after the last email)
  const [refused, setRefused] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: getListInvitationsQueryKey(organisation.id) })

  function noted(made: InvitationOut[]) {
    setFailed((prev) => {
      const next = { ...prev }
      for (const m of made) {
        if (m.email_sent?.sent === false) next[m.id] = m.email_sent.error ?? ""
        else if (m.email_sent?.sent) delete next[m.id]
      }
      return next
    })
  }

  async function onSendAgain(id: string) {
    setRefused((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== id)))
    try {
      noted([await send.mutateAsync({ organisationId: organisation.id, invitationId: id })])
      await refresh()
    } catch (e) {
      setRefused((prev) => ({ ...prev, [id]: errorMessage(e) }))
    }
  }

  async function onRevoke(id: string, email: string) {
    if (!confirm(`Revoke the invitation for ${email}? Its link stops working.`)) return
    setError(null)
    try {
      await revoke.mutateAsync({ organisationId: organisation.id, invitationId: id })
      await refresh()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Invitations</CardTitle>
        <CardDescription>
          {emailed
            ? "Each address gets an email with its link, from housegen with you as the reply address; the link is here to copy too."
            : "Each address gets a link to copy and send yourself: housegen sends no email."}{" "}
          Whoever opens the link and signs in joins as a member, whatever account they use.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <InviteForm
          organisationId={organisation.id}
          submitLabel="Invite"
          onCreated={(made) => {
            setFresh((prev) => [...prev, ...made.map((m) => m.id)])
            noted(made)
            void refresh()
          }}
        />
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Pending</h3>
          {invitations.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
          {invitations.error ? <p className="text-sm text-destructive">{errorMessage(invitations.error)}</p> : null}
          {invitations.data?.length === 0 && <p className="text-sm text-muted-foreground">No pending invitation.</p>}
          {invitations.data?.map((i) => (
            <div key={i.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2">
              <div className="min-w-0 flex-1 basis-48">
                <div className="truncate text-sm">
                  {i.email}
                  {fresh.includes(i.id) && (
                    <Badge variant="success" className="ml-2">
                      new
                    </Badge>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">until {formatDay(i.expires_at)}</div>
                <EmailStatus invitation={i} failed={failed[i.id]} />
              </div>
              <CopyLinkButton url={i.url} />
              {emailed && (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Send the invitation for ${i.email} again`}
                  disabled={send.isPending}
                  onClick={() => void onSendAgain(i.id)}
                >
                  <Mail />
                  Send again
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Revoke the invitation for ${i.email}`}
                disabled={revoke.isPending}
                onClick={() => void onRevoke(i.id, i.email)}
              >
                <Trash2 />
                Revoke
              </Button>
              {refused[i.id] && <p className="basis-full text-xs text-destructive">{refused[i.id]}</p>}
            </div>
          ))}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
      </CardContent>
    </Card>
  )
}
