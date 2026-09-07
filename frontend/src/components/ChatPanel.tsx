import { useState } from "react"
import { Loader2, MessageCircleQuestion, Plus, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { ChatMessageOut, SceneVersionOut } from "@/api/model"
import { cn } from "@/lib/utils"

export function ChatPanel({
  messages,
  versions = [],
  busy,
  disabled,
  onSend,
}: {
  messages: ChatMessageOut[]
  versions?: SceneVersionOut[]
  busy: boolean
  disabled?: boolean
  onSend: (text: string) => Promise<void>
}) {
  const [text, setText] = useState("")
  async function send(t = text) {
    t = t.trim()
    if (!t || busy) return
    setText("")
    await onSend(t)
  }
  // the builder's suggestions/questions live on the version its message produced; show them
  // under the LAST assistant message only (earlier ones were either taken or are stale)
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant")
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {messages.length === 0 && (
          <p className="text-muted-foreground">
            Ask for changes in plain language: “make the shutters anthracite”, “add a pergola on the south terrace”,
            “the east façade has one more window on the first floor”.
          </p>
        )}
        {messages.map((m) => {
          const version = m.role === "assistant" && m.version_number != null ? versions.find((v) => v.number === m.version_number) : undefined
          const showChips = m === lastAssistant && version !== undefined && !busy && !disabled
          return (
            <div key={m.id} className={cn("flex flex-col", m.role === "user" ? "items-end" : "items-start")}>
              <div
                className={cn(
                  "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2",
                  m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
                )}
              >
                {m.content}
                {m.role === "assistant" && m.version_number != null && (
                  <div className="mt-1 text-[11px] opacity-70">→ version {m.version_number}</div>
                )}
              </div>
              {showChips && (
                <FinishChips
                  suggestions={version.suggestions ?? []}
                  questions={version.questions ?? []}
                  onSend={(t) => void send(t)}
                  onPrefill={(t) => setText(t)}
                />
              )}
            </div>
          )
        })}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" /> the agent is working…
          </div>
        )}
      </div>
      <div className="flex gap-2 border-t p-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              void send()
            }
          }}
          placeholder={disabled ? "Generate the scene first" : "Describe a modification…"}
          disabled={disabled || busy}
          className="min-h-[44px] resize-none"
          rows={2}
        />
        <Button size="icon" onClick={() => void send()} disabled={disabled || busy || !text.trim()} aria-label="Send">
          <Send />
        </Button>
      </div>
    </div>
  )
}

/** Compose one modification request from the ticked suggestions (+ an optional free line). */
export function composeAdditions(items: string[], extra = ""): string {
  const lines = ["Add these to the scene, as seen in the photographs:", ...items.map((s) => `- ${s}`)]
  if (extra.trim()) lines.push("", extra.trim())
  return lines.join("\n")
}

/**
 * The builder's optional additions as tick-able chips (selection → one modify request) and its
 * questions as chips that prefill the composer with "Q: … A: " for the owner to answer.
 */
function FinishChips({
  suggestions,
  questions,
  onSend,
  onPrefill,
}: {
  suggestions: string[]
  questions: string[]
  onSend: (text: string) => void
  onPrefill: (text: string) => void
}) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set())
  const [extra, setExtra] = useState("")
  if (suggestions.length === 0 && questions.length === 0) return null
  const toggle = (s: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  return (
    <div className="mt-1.5 max-w-[85%] space-y-2 text-xs">
      {questions.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-1 text-muted-foreground">
            <MessageCircleQuestion className="size-3.5" /> The builder asks
          </div>
          <div className="flex flex-wrap gap-1">
            {questions.map((q) => (
              <button
                key={q}
                type="button"
                className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-left text-amber-900 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
                title="Answer this question"
                onClick={() => onPrefill(`Q: ${q}\nA: `)}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground">Left out on purpose, tick to add</div>
          <div className="flex flex-wrap gap-1">
            {suggestions.map((s) => {
              const on = picked.has(s)
              return (
                <button
                  key={s}
                  type="button"
                  aria-pressed={on}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-left hover:bg-accent",
                    on && "border-primary bg-primary/10 text-primary",
                  )}
                  onClick={() => toggle(s)}
                >
                  {on ? "✓ " : ""}
                  {s}
                </button>
              )
            })}
          </div>
          {picked.size > 0 && (
            <div className="flex items-center gap-1.5">
              <input
                value={extra}
                onChange={(e) => setExtra(e.target.value)}
                placeholder="anything else? (optional)"
                className="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs"
              />
              <Button size="sm" className="h-7" onClick={() => onSend(composeAdditions([...suggestions.filter((s) => picked.has(s))], extra))}>
                <Plus /> Add {picked.size}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
