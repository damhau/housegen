import { useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, ChevronDown, ChevronRight, Hammer, ImagePlus, Loader2, MessageCircleQuestion, Play, ScanSearch, Send, Square, Wrench, X } from "lucide-react"
import { useJobEvents } from "@/api/endpoints/projects/projects"
import type { ChatMessageOut, IntakeAnswer, IntakeOut, JobOut, SceneVersionOut } from "@/api/model"
import { JobTimeline, ScoreBadge } from "@/components/JobTimeline"
import { Markdown } from "@/components/Markdown"
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
  estimate = null,
  onSend,
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
  /** "~8 min · ~$2": what the next modification would roughly cost (#18) */
  estimate?: string | null
  /** a modification request, with optional photos of the detail to change (#8) and, with
   *  `applyReviewOf`, the stored review findings of that version in the same request */
  onSend: (text: string, photos?: File[], keep?: boolean, applyReviewOf?: number) => Promise<void>
  /** answers to the intake's questions: starts the build */
  onAnswer: (answers: IntakeAnswer[], notes: string) => Promise<void>
}) {
  const [text, setText] = useState("")
  // photos attached to the next request; kept as reference photos of the project unless unticked
  const [files, setFiles] = useState<File[]>([])
  const [keep, setKeep] = useState(true)
  const fileUrls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files])
  useEffect(() => () => fileUrls.forEach((u) => URL.revokeObjectURL(u)), [fileUrls])
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

  function addFiles(list: FileList | File[] | null) {
    if (!list) return
    const picked = Array.from(list).filter((f) => f.type.startsWith("image/"))
    if (picked.length > 0) setFiles((prev) => [...prev, ...picked].slice(0, 12))
  }

  async function send(t = text, applyReviewOf?: number) {
    t = t.trim()
    if ((!t && applyReviewOf === undefined) || busy || disabled) return
    const photos = t === text.trim() ? files : []
    setText("")
    if (photos.length > 0) setFiles([])
    await onSend(t, photos, keep, applyReviewOf)
  }

  const placeholder = disabled
    ? "Generate the scene first"
    : busy
      ? "The agent is working… you can write when it is done"
      : files.length > 0
        ? "What should change, as the photos show it?"
        : "Ask for a change… (drop or paste a photo of the detail to change)"

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
              {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
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
              // chips need a version to modify (busy || disabled); the intake form only needs no job running
              anyJobRunning={busy}
              busy={busy || disabled}
              onSend={(txt, applyReviewOf) => void send(txt, applyReviewOf)}
              onAnswer={onAnswer}
            />
          ))}
        </div>
        <div ref={bottomRef} />
      </div>
      <div
        className="border-t p-2"
        onDragOver={(e) => {
          if (!disabled && !busy) e.preventDefault()
        }}
        onDrop={(e) => {
          if (disabled || busy) return
          e.preventDefault()
          addFiles(e.dataTransfer.files)
        }}
      >
        {files.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {files.map((f, i) => (
              <div key={`${f.name}-${i}`} className="group relative size-14 overflow-hidden rounded border">
                <img src={fileUrls[i]} alt="" className="size-full object-cover" />
                <button
                  type="button"
                  aria-label="Remove photo"
                  className="absolute right-0.5 top-0.5 hidden rounded-full bg-background/90 p-0.5 group-hover:block"
                  onClick={() => setFiles((prev) => prev.filter((_, k) => k !== i))}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
            <label className="ml-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
              keep as reference photos of the house
            </label>
          </div>
        )}
        <div className="flex gap-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
            onPaste={(e) => {
              const items = Array.from(e.clipboardData.files)
              if (items.some((f) => f.type.startsWith("image/"))) {
                e.preventDefault()
                addFiles(items)
              }
            }}
            placeholder={placeholder}
            disabled={disabled || busy}
            className="min-h-[44px] resize-none"
            rows={2}
          />
          <div className="flex flex-col gap-1">
            <Button size="icon" onClick={() => void send()} disabled={disabled || busy || !text.trim()} aria-label="Send">
              <Send />
            </Button>
            <label
              className={cn(
                "grid size-9 cursor-pointer place-items-center rounded-md border text-muted-foreground hover:bg-accent",
                (disabled || busy) && "pointer-events-none opacity-50",
              )}
              title="Attach photos of the detail to change"
            >
              <ImagePlus className="size-4" />
              <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => addFiles(e.target.files)} />
            </label>
          </div>
        </div>
        {estimate && !disabled && !busy && <div className="mt-1 text-[11px] text-muted-foreground">A modification takes {estimate}</div>}
      </div>
    </div>
  )
}

/** user: plain text as typed; assistant: markdown (see Markdown.tsx) */
function Bubble({ role, children }: { role: string; children: React.ReactNode }) {
  return (
    <div className={cn("flex", role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2",
          role === "user" ? "whitespace-pre-wrap bg-primary text-primary-foreground" : "bg-muted",
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
  anyJobRunning,
  busy,
  onSend,
  onAnswer,
}: {
  projectId: string
  turn: Turn
  liveJob: LiveJob
  isLastAnswer: boolean
  currentVersion: number
  hasPhotos: boolean
  intakeToAnswer: IntakeOut | null
  /** some job is running or being started (this block's own state is `running` below) */
  anyJobRunning: boolean
  /** no interaction possible: a job is running, or there is no version to modify yet */
  busy: boolean
  onSend: (text: string, applyReviewOf?: number) => void
  onAnswer: (answers: IntakeAnswer[], notes: string) => Promise<void>
}) {
  const { job, userMsg, assistantMsg, version } = turn
  const followed = liveJob.jobId === job.id
  // interrupted = the server is restarting and will resume it with the same id: still in progress
  const running = job.status === "running" || job.status === "queued" || job.status === "interrupted"
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
  // the review's findings can be applied while this version is the current scene
  const review = version !== undefined && issues > 0 && version.number === currentVersion ? { version: version.number, issues } : null

  return (
    <div className="space-y-2">
      {userMsg ? (
        <Bubble role="user">
          {userMsg.content}
          {(userMsg.attachments ?? []).length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {(userMsg.attachments ?? []).map((url) => (
                <a key={url} href={url} target="_blank" rel="noreferrer">
                  <img src={url} alt="attached photo" className="h-16 rounded border border-primary-foreground/30 object-cover" />
                </a>
              ))}
            </div>
          )}
        </Bubble>
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
          {job.status === "interrupted" ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
          ) : running ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
          ) : job.status === "failed" ? (
            <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
          ) : job.status === "cancelled" ? (
            <Square className="size-3.5 shrink-0 text-muted-foreground" />
          ) : job.kind === "intake" ? (
            <ScanSearch className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <Hammer className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="font-medium">
            {KIND_LABEL[job.kind] ?? job.kind}
            {job.status === "interrupted" ? " waiting for the server" : running ? " in progress" : job.status === "failed" ? " failed" : job.status === "cancelled" ? " stopped" : ""}
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
            <Markdown text={answer} />
            {answerVersion != null && <div className="mt-1 text-[11px] opacity-70">→ version {answerVersion}</div>}
          </Bubble>
          {intakeToAnswer && !anyJobRunning && <IntakeForm intake={intakeToAnswer} onSubmit={onAnswer} />}
          {isLastAnswer && !busy && (
            <ContinueForm
              key={version?.number ?? job.id}
              suggestions={version?.suggestions ?? []}
              questions={version?.questions ?? []}
              review={review}
              onSend={onSend}
            />
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
 * What the owner sends back after a version, as ONE request and one job: the review's findings
 * (ticked by default), the builder's optional additions (tick to add), answers to its questions,
 * and a free line. These used to be three competing buttons, each starting its own job, so only
 * one of them could ever be taken.
 */
function ContinueForm({
  suggestions,
  questions,
  review,
  onSend,
}: {
  suggestions: string[]
  questions: string[]
  /** the current version's review with findings, or null */
  review: { version: number; issues: number } | null
  onSend: (text: string, applyReviewOf?: number) => void
}) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set())
  const [answers, setAnswers] = useState<string[]>(() => questions.map(() => ""))
  const [applyReview, setApplyReview] = useState(review !== null)
  const [extra, setExtra] = useState("")
  if (suggestions.length === 0 && questions.length === 0 && review === null) return null
  const toggle = (s: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })
  const answered = questions.map((q, i) => [q, (answers[i] ?? "").trim()] as const).filter(([, a]) => a)
  const withReview = applyReview && review !== null
  const parts = [
    withReview && `the review's ${review.issues} finding${review.issues > 1 ? "s" : ""}`,
    picked.size > 0 && `${picked.size} addition${picked.size > 1 ? "s" : ""}`,
    answered.length > 0 && `${answered.length} answer${answered.length > 1 ? "s" : ""}`,
    extra.trim() && "your note",
  ].filter((x): x is string => Boolean(x))
  const submit = () => {
    const blocks: string[] = []
    if (answered.length > 0) blocks.push("Answers to your questions:\n" + answered.map(([q, a]) => `Q: ${q}\nA: ${a}`).join("\n"))
    if (picked.size > 0) blocks.push(composeAdditions(suggestions.filter((s) => picked.has(s))))
    if (extra.trim()) blocks.push(extra.trim())
    onSend(blocks.join("\n\n"), withReview ? review.version : undefined)
  }
  return (
    <div className="mt-1.5 max-w-[85%] space-y-2 text-xs">
      {review !== null && (
        <label className="flex cursor-pointer items-center gap-1.5">
          <input type="checkbox" checked={applyReview} onChange={(e) => setApplyReview(e.target.checked)} />
          <Wrench className="size-3.5" /> Apply the review's {review.issues} finding{review.issues > 1 ? "s" : ""}
        </label>
      )}
      {questions.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-1 text-muted-foreground">
            <MessageCircleQuestion className="size-3.5" /> The builder asks
          </div>
          {questions.map((q, i) => (
            <div key={q} className="space-y-0.5">
              <div className="text-amber-900 dark:text-amber-100">{q}</div>
              <input
                value={answers[i] ?? ""}
                onChange={(e) => setAnswers((prev) => prev.map((a, j) => (j === i ? e.target.value : a)))}
                placeholder="your answer (optional)"
                className="h-7 w-full rounded-md border bg-background px-2 text-xs"
              />
            </div>
          ))}
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
        </div>
      )}
      <div className="flex items-center gap-1.5">
        <input
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder="anything else? (optional)"
          className="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs"
        />
        <Button size="sm" className="h-7" disabled={parts.length === 0} title={parts.length ? `One request: ${parts.join(", ")}` : "Nothing selected"} onClick={submit}>
          <Send /> Send{parts.length > 0 && ` (${parts.length})`}
        </Button>
      </div>
      {parts.length > 0 && <div className="text-[11px] text-muted-foreground">One request, one job: {parts.join(", ")}.</div>}
    </div>
  )
}
