// Fetch-based mutator used by every orval-generated call.
// Returns the parsed body on 2xx, throws ApiError otherwise.

export class ApiError extends Error {
  status: number
  code: string
  details: unknown
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

export const httpClient = async <T>(url: string, options: RequestInit = {}): Promise<T> => {
  const headers = new Headers(options.headers ?? {})
  const isForm = typeof FormData !== "undefined" && options.body instanceof FormData
  if (!isForm && options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  if (isForm) headers.delete("Content-Type") // let the browser set the multipart boundary

  const res = await fetch(url, { ...options, headers })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let body: unknown = undefined
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; details?: unknown } } | undefined)?.error
    const detail = (body as { detail?: unknown } | undefined)?.detail
    const message =
      err?.message ??
      (typeof detail === "string" ? detail : Array.isArray(detail) ? JSON.stringify(detail) : undefined) ??
      `${res.status} ${res.statusText}`
    throw new ApiError(res.status, err?.code ?? "http_error", message, err?.details ?? detail)
  }
  return body as T
}

export default httpClient
