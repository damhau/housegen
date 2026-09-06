import { useEffect, useRef, useState } from "react"
import type { JobEventOut } from "@/api/model"

export type JobEvent = JobEventOut

/** Live state of the LLM call currently in flight (transient `llm_progress` events). */
export interface LlmProgress {
  role: "analyst" | "builder" | "critic" | string
  step: number | null
  phase: "thinking" | "writing" | "tool_call" | string
  tool_name: string | null
  output_tokens: number
  estimated: boolean
  elapsed_s: number
  done: boolean
}

const PERSISTED = [
  "phase", "builder_text", "builder_step", "builder_done", "render", "version", "critic", "usage", "done", "error",
]

/**
 * Follows a job's server-sent event stream. Persisted events are replayed by the
 * backend first, then live ones. Transient progress (`llm_progress`, `llm_thought`,
 * `builder_delta`) is kept in separate state and never enters the persisted list.
 */
export function useJobStream(projectId: string, jobId: string | null | undefined, onEnd?: () => void) {
  const [events, setEvents] = useState<JobEvent[]>([])
  const [live, setLive] = useState(false)
  const [progress, setProgress] = useState<LlmProgress | null>(null)
  const [liveText, setLiveText] = useState("")
  const [liveThought, setLiveThought] = useState("")
  const onEndRef = useRef(onEnd)
  onEndRef.current = onEnd

  useEffect(() => {
    setEvents([])
    setProgress(null)
    setLiveText("")
    setLiveThought("")
    if (!jobId) return
    const es = new EventSource(`/api/v1/projects/${projectId}/jobs/${jobId}/stream`)
    setLive(true)

    const persisted = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as JobEvent
        setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev]))
        // a persisted builder event supersedes whatever was streaming
        if (ev.type === "builder_text" || ev.type === "builder_step" || ev.type === "builder_done" || ev.type === "phase") {
          setLiveText("")
        }
        if (ev.type === "done" || ev.type === "error") {
          setProgress(null)
          setLiveThought("")
        }
      } catch {
        /* ignore malformed */
      }
    }
    const onProgress = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as { payload: LlmProgress }
        setProgress(ev.payload)
      } catch {
        /* ignore */
      }
    }
    const onThought = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as { payload: { text: string; reset: boolean } }
        setLiveThought((t) => (ev.payload.reset ? "" : (t + ev.payload.text).slice(-3000)))
      } catch {
        /* ignore */
      }
    }
    const onDelta = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as { payload: { text: string } }
        setLiveText((t) => (t + ev.payload.text).slice(-4000))
      } catch {
        /* ignore */
      }
    }
    for (const t of PERSISTED) es.addEventListener(t, persisted as EventListener)
    es.addEventListener("llm_progress", onProgress as EventListener)
    es.addEventListener("llm_thought", onThought as EventListener)
    es.addEventListener("builder_delta", onDelta as EventListener)
    es.addEventListener("end", () => {
      setLive(false)
      setProgress(null)
      setLiveText("")
      setLiveThought("")
      es.close()
      onEndRef.current?.()
    })
    es.onerror = () => {
      // the browser retries automatically; when the job is over the server closes and we stop
    }
    return () => {
      es.close()
      setLive(false)
    }
  }, [projectId, jobId])

  return { events, live, progress, liveText, liveThought }
}
