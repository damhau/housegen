import { useState } from "react"
import { UserPlus } from "lucide-react"
import { useCreateInvitation } from "@/api/endpoints/accounts/accounts"
import type { InvitationOut } from "@/api/model"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/utils"

// the backend's own check (InvitationIn): one @, a dot after it, no spaces
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function parseEmails(text: string): { valid: string[]; invalid: string[] } {
  const words = text
    .split(/[\s,;]+/)
    .map((w) => w.trim().replace(/^<|>$/g, ""))
    .filter(Boolean)
  const valid = [...new Set(words.filter((w) => EMAIL.test(w)).map((w) => w.toLowerCase()))]
  return { valid, invalid: words.filter((w) => !EMAIL.test(w)) }
}

/** Email addresses → invitation links, emailed when the server sends email (`email_sent` on each). */
export function InviteForm({
  organisationId,
  onCreated,
  submitLabel = "Create invitation links",
}: {
  organisationId: string
  onCreated: (invitations: InvitationOut[]) => void
  submitLabel?: string
}) {
  const [text, setText] = useState("")
  const [error, setError] = useState<string | null>(null)
  const create = useCreateInvitation()
  const { valid, invalid } = parseEmails(text)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (invalid.length > 0) return setError(`Not an email address: ${invalid.join(", ")}`)
    if (valid.length === 0) return
    try {
      const made = await Promise.all(valid.map((email) => create.mutateAsync({ organisationId, data: { email } })))
      setText("")
      onCreated(made)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="grid gap-2">
      <label className="grid gap-1 text-sm">
        <span className="text-xs text-muted-foreground">Email addresses, one per line or separated by commas</span>
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder={"anna@agency.ch\nmarc@agency.ch"}
          aria-label="Email addresses to invite"
        />
      </label>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div>
        <Button type="submit" size="sm" disabled={valid.length === 0 || create.isPending}>
          <UserPlus />
          {create.isPending ? "Creating…" : valid.length > 1 ? `${submitLabel} (${valid.length})` : submitLabel}
        </Button>
      </div>
    </form>
  )
}
