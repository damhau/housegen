import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Message of anything thrown by the API client (ApiError, Error, or an unknown value). */
export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === "string") return e
  return "Unexpected error"
}

/**
 * Parse an API timestamp. The backend sends aware UTC ("…Z"); a bare ISO string without
 * zone (old rows, other tools) is UTC too, not local time as `new Date()` would assume.
 */
export function parseIso(iso: string): Date {
  return new Date(/(?:Z|[+-]\d\d:?\d\d)$/i.test(iso) ? iso : `${iso}Z`)
}

export function formatDate(iso: string) {
  return parseIso(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export function relTime(iso: string) {
  const s = (Date.now() - parseIso(iso).getTime()) / 1000
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
