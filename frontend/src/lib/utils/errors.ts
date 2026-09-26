/**
 * API error type + user-facing message mapping.
 *
 * `fetch()` rejects with a bare TypeError on network failure (DNS, CORS,
 * connection refused) — that is the "backend down" case and must produce the
 * exact copy `Could not connect to the backend` (test contract).
 */

export class ApiError extends Error {
  readonly status: number
  readonly statusText: string
  /** The server's own explanation, present only when it sent one. */
  readonly detail: string | undefined

  constructor(status: number, statusText: string, detail?: string) {
    super(detail ?? `HTTP ${status}: ${statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.detail = detail
  }
}

export function isBackendDown(error: unknown): boolean {
  return error instanceof TypeError
}

/**
 * Message to show for a failed request, preferring the server's own words.
 *
 * FastAPI sends a human-readable `detail` for 400/409 (a duplicate folder name,
 * say), which is always more specific than a generic mapping. `ApiError` only
 * carries a status line when the server sent no detail, so that case falls
 * through to `toUserMessage`.
 *
 * "Did the server send a detail?" is answered from the `detail` field, not by
 * comparing the message against the `HTTP <status>: <statusText>` line the
 * constructor would have generated. That comparison re-derived the same magic
 * string in two places and would have silently started lying the moment either
 * one was reworded.
 */
export function toDetailMessage(error: unknown): string {
  if (error instanceof ApiError && error.detail) {
    return error.detail
  }
  return toUserMessage(error)
}

export function toUserMessage(error: unknown): string {
  if (error instanceof TypeError) {
    return 'Could not connect to the backend'
  }
  if (error instanceof ApiError) {
    if (error.status === 400) return 'The request was invalid'
    if (error.status === 404) return 'Not found'
    if (error.status === 413) return 'That file is too large'
    if (error.status === 415) return 'That file type is not supported'
    if (error.status >= 500) return 'The server ran into a problem — try again'
    return `Request failed (HTTP ${error.status})`
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Something went wrong'
}
