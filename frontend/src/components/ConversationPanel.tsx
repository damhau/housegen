import { useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, ChevronDown, ChevronRight, Hammer, Loader2, MessageCircleQuestion, Play, Plus, ScanSearch, Send, Wrench } from "lucide-react"
import { useJobEvents } from "@/api/endpoints/projects/projects"
import type { ChatMessageOut, IntakeAnswer, IntakeOut, JobOut, SceneVersionOut } from "@/api/model"
import { JobTimeline, ScoreBadge } from "@/components/JobTimeline"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { JobEvent, LlmProgress } from "@/hooks/useJobStream"
import { cn, parseIso, relTime } from "@/lib/utils"

/** Live state of the job the page follows (from useJobStream). */
export interface LiveJob {
  jobId: string | null
  events: JobEvent[]
  live: boolean
  progress: LlmProgress | null
  liveText: string
  liveThought: string
  elapsed: string | null
}

/** One job as one turn of the conversation: the request that started it, the agent's work, its answer. */
interface Turn {
  job: JobOut
  userMsg?: ChatMessageOut
  assistantMsg?: ChatMessageOut
  version?: SceneVersionOut
  createdAt: number
}

/**
 * The single conversation view: every job is a block (user bubble → collapsible agent turn →
 * assistant bubble with chips), in chronological order, with the composer at the bottom.
 * The followed job (running, or the latest) streams live and is expanded by default.
 */
export function ConversationPanel({
  projectId,
  jobs,
  messages,
  versions,
  currentVersion,
  hasPhotos,
  intake,
  liveJob,
  busy,
  disabled,
  onSend,
  onFix,
  onAnswer,
}: {
  projectId: string
  jobs: JobOut[]
  messages: ChatMessageOut[]
  versions: SceneVersionOut[]
  currentVersion: number
  hasPhotos: boolean
  /** the intake's reading of the plans (projects without photos), with its questions */
  intake: IntakeOut | null
  liveJob: LiveJob
  busy: boolean
  /** no version yet: nothing to modify */
  disabled: boolean
  onSend: (text: string) => Promise<void>
  onFix: (version: number) => void
  /** answers to the intake's questions: starts the build */
  onAnswer: (answers: IntakeAnswer[], notes: string) => Promise<void>
}) {
  const [text, setText] = useState("")
  const bottomRef = useRef<HTMLDivElement>(null)

  const turns = useMemo<Turn[]>(() => {
    const byJob = (role: string, jobId: string) => messages.find((m) => m.role === role && m.job_id === jobId)
    return [...jobs]
      .sort((a, b) => parseIso(a.created_at).getTime() - parseIso(b.created_at).getTime())
      .map((job) => ({
        job,
        userMsg: byJob("user", job.id),
        assistantMsg: byJob("assistant", job.id),
        version: job.result_version != null ? versions.find((v) => v.number === job.result_version) : undefined,
        createdAt: parseIso(job.created_at).getTime(),
      }))
  }, [jobs, messages, versions])
  // messages that belong to no job (none today; kept so nothing in the history is ever hidden)
  const orphans = useMemo(() => messages.filter((m) => !m.job_id || !jobs.some((j) => j.id === m.job_id)), [messages, jobs])
  const lastAssistantTurn = [...turns].reverse().find((t) => t.assistantMsg || t.version)
  const lastJob = turns[turns.length - 1]?.job

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [turns.length, liveJob.jobId])

  async function send(t = text) {
    t = t.trim()
    if (!t || busy || disabled) return
    setText("")
    await onSend(t)
  }

  const placeholder = disabled ? "Generate the scene first" : busy ? "The agent is working… you can write when it is done" : "Ask for a change…"

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-3 text-sm">
        {turns.length === 0 && orphans.length === 0 && (
          <p className="text-muted-foreground">
            No activity yet. Press <span className="font-medium">{hasPhotos ? "Generate" : "Read the plans"}</span> and the agent's work will appear here;
            afterwards, ask for changes in plain language: “make the shutters anthracite”, “add a pergola on the south terrace”.
          </p>
        )}
        <div className="space-y-4">
          {orphans.map((m) => (
            <Bubble key={m.id} role={m.role}>
              {m.content}
            </Bubble>
          ))}
          {turns.map((t) => (
            <JobBlock
              key={t.job.id}
              projectId={projectId}
              turn={t}
              liveJob={liveJob}
              isLastAnswer={t === lastAssistantTurn}
              currentVersion={currentVersion}
              hasPhotos={hasPhotos}
              // the intake's questions are answered on its own turn, only while nothing came after it
              intakeToAnswer={t.job.kind === "intake" && t.job.status === "done" && t.job === lastJob && intake?.job_id === t.job.id ? intake : null}
              busy={busy || disabled}
              onSend={(txt) => void send(txt)}
              onPrefill={setText}
              onFix={onFix}
              onAnswer={onAnswer}
            />
          ))}
        </div>
        <div ref={bottomRef} />
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
          placeholder={placeholder}
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

function Bubble({ role, children }: { role: string; children: React.ReactNode }) {
  return (
    <div className={cn("flex", role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2",
          role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {children}
      </div>
    </div>
  )
}

function duration(job: JobOut): string | null {
  if (!job.finished_at) return null
  const s = Math.max(0, Math.round((parseIso(job.finished_at).getTime() - parseIso(job.created_at).getTime()) / 1000))
  return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`
}

const KIND_LABEL: Record<string, string> = { intake: "Reading the plans", generate: "Generation", modify: "Modification" }

function JobBlock({
  projectId,
  turn,
  liveJob,
  isLastAnswer,
  currentVersion,
  hasPhotos,
  intakeToAnswer,
  busy,
  onSend,
  onPrefill,
  onFix,
  onAnswer,
}: {
  projectId: string
  turn: Turn
  liveJob: LiveJob
  isLastAnswer: boolean
  currentVersion: number
  hasPhotos: boolean
  intakeToAnswer: IntakeOut | null
  busy: boolean
  onSend: (text: string) => void
  onPrefill: (text: string) => void
  onFix: (version: number) => void
  onAnswer: (answers: IntakeAnswer[], notes: string) => Promise<void>
}) {
  const { job, userMsg, assistantMsg, version } = turn
  const followed = liveJob.jobId === job.id
  const running = job.status === "running" || job.status === "queued"
  const [open, setOpen] = useState(followed)
  useEffect(() => {
    if (followed && running) setOpen(true)
  }, [followed, running])
  // older jobs: persisted events, fetched only when the block is opened
  const stored = useJobEvents(projectId, job.id, undefined, { query: { enabled: open && !followed } })
  const events = followed ? liveJob.events : (stored.data ?? [])

  const requestLabel =
    job.kind === "generate" ? `Build the house from the plans${hasPhotos ? " and photos" : ""}` : job.kind === "intake" ? "Read the plans" : job.request_text
  // the answer is the assistant's chat message; older runs stored none, the version summary says the same
  const answer = assistantMsg?.content ?? version?.summary
  const answerVersion = assistantMsg?.version_number ?? version?.number
  const issues = version?.critique?.issues.length ?? 0
  const canFix = version !== undefined && issues > 0 && version.number === currentVersion && !busy && isLastAnswer

  return (
    <div className="space-y-2">
      {userMsg ? (
        <Bubble role="user">{userMsg.content}</Bubble>
      ) : (
        <div className="flex justify-end">
          <span className="rounded-lg bg-primary/10 px-3 py-1.5 text-xs text-primary">{requestLabel}</span>
        </div>
      )}

      <div className="rounded-lg border">
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent/40"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
          {running ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
          ) : job.status === "failed" ? (
            <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
          ) : job.kind === "intake" ? (
            <ScanSearch className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <Hammer className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="font-medium">
            {KIND_LABEL[job.kind] ?? job.kind}
            {running ? " in progress" : job.status === "failed" ? " failed" : ""}
          </span>
          <span className="text-muted-foreground">
            {running && followed && liveJob.elapsed ? <span className="font-mono tabular-nums">{liveJob.elapsed}</span> : duration(job)}
            {!running && ` · ${relTime(job.created_at)}`}
            {version && ` · v${version.number}`}
          </span>
          {version?.critic_score != null && <ScoreBadge score={version.critic_score} />}
          {version?.render_urls.aerial && !open && <img src={version.render_urls.aerial} alt="" className="ml-auto h-7 w-11 rounded border object-cover" />}
        </button>
        {open && (
          <div className="border-t">
            {!followed && stored.isPending ? (
              <p className="p-3 text-xs text-muted-foreground">Loading…</p>
            ) : (
              <JobTimeline
                events={events}
                live={followed && liveJob.live}
                progress={followed ? liveJob.progress : null}
                liveText={followed ? liveJob.liveText : ""}
                liveThought={followed ? liveJob.liveThought : ""}
              />
            )}
          </div>
        )}
      </div>

      {job.status === "failed" && job.error && !open && <div className="text-xs text-destructive">{job.error}</div>}

      {answer !== undefined && (
        <div className="flex flex-col items-start">
          <Bubble role="assistant">
            {answer}
            {answerVersion != null && <div className="mt-1 text-[11px] opacity-70">→ version {answerVersion}</div>}
          </Bubble>
          {intakeToAnswer && !busy && <IntakeForm intake={intakeToAnswer} onSubmit={onAnswer} />}
          {isLastAnswer && !busy && (
            <FinishChips
              suggestions={version?.suggestions ?? []}
              questions={version?.questions ?? []}
              onSend={onSend}
              onPrefill={onPrefill}
            />
          )}
          {canFix && (
            <div className="mt-1.5">
              <Button size="sm" variant="outline" onClick={() => onFix(version.number)}>
                <Wrench /> Apply the review's {issues} finding{issues > 1 ? "s" : ""}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * The intake's questions as a small form, each answer prefilled with the model's best guess so
 * one click accepts the defaults; submitting starts the build with the answers in the brief.
 */
function IntakeForm({ intake, onSubmit }: { intake: IntakeOut; onSubmit: (answers: IntakeAnswer[], notes: string) => Promise<void> }) {
  const questions = intake.questions ?? []
  const [answers, setAnswers] = useState<string[]>(() => questions.map((q) => q.suggested))
  const [notes, setNotes] = useState("")
  const [sending, setSending] = useState(false)
  async function submit() {
    setSending(true)
    try {
      await onSubmit(
        questions.map((q, i) => ({ question: q.question, answer: answers[i] ?? "" })),
        notes,
      )
    } finally {
      setSending(false)
    }
  }
  return (
    <div className="mt-1.5 w-full max-w-[85%] space-y-2 rounded-lg border border-amber-300 bg-amber-50/60 p-3 text-xs dark:border-amber-700 dark:bg-amber-950/40">
      {questions.length > 0 && (
        <div className="flex items-center gap-1 font-medium text-amber-900 dark:text-amber-100">
          <MessageCircleQuestion className="size-3.5" /> Before building, the drawings cannot tell me
        </div>
      )}
      {questions.map((q, i) => (
        <label key={q.question} className="grid gap-0.5">
          <span className="font-medium">{q.question}</span>
          <span className="text-muted-foreground">{q.why}</span>
          <input
            value={answers[i] ?? ""}
            onChange={(e) => setAnswers((prev) => prev.map((a, k) => (k === i ? e.target.value : a)))}
            className="h-7 rounded-md border bg-background px-2 text-xs"
          />
        </label>
      ))}
      <label className="grid gap-0.5">
        <span className="font-medium">Anything else? <span className="font-normal text-muted-foreground">(optional)</span></span>
        <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="min-h-0 bg-background text-xs" placeholder="materials, colours, what changed since the plans…" />
      </label>
      <Button size="sm" className="h-7" onClick={() => void submit()} disabled={sending}>
        {sending ? <Loader2 className="animate-spin" /> : <Play />} Start the build
      </Button>
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
                  className={cn("rounded-full border px-2.5 py-1 text-left hover:bg-accent", on && "border-primary bg-primary/10 text-primary")}
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
              <Button size="sm" className="h-7" onClick={() => onSend(composeAdditions(suggestions.filter((s) => picked.has(s)), extra))}>
                <Plus /> Add {picked.size}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
