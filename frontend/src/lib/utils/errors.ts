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

  constructor(status: number, statusText: string, message?: string) {
    super(message ?? `HTTP ${status}: ${statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
  }
}

export function isBackendDown(error: unknown): boolean {
  return error instanceof TypeError
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
