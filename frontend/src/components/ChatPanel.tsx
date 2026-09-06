import { useState } from "react"
import { Loader2, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { ChatMessageOut } from "@/api/model"
import { cn } from "@/lib/utils"

export function ChatPanel({
  messages,
  busy,
  disabled,
  onSend,
}: {
  messages: ChatMessageOut[]
  busy: boolean
  disabled?: boolean
  onSend: (text: string) => Promise<void>
}) {
  const [text, setText] = useState("")
  async function send() {
    const t = text.trim()
    if (!t || busy) return
    setText("")
    await onSend(t)
  }
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {messages.length === 0 && (
          <p className="text-muted-foreground">
            Ask for changes in plain language: “make the shutters anthracite”, “add a pergola on the south terrace”,
            “the east façade has one more window on the first floor”.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
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
          </div>
        ))}
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
