/**
 * Every endpoint must report failure the same way.
 *
 * Four of them — uploadDocument, uploadVoice, previewVoice and createTextBook —
 * rejected with a bare `Error` carrying an `HTTP <status>: <statusText>` string
 * while everything else went through `toApiError`. Two things were wrong with
 * that. The user saw the raw status line, because `toUserMessage` returns
 * `error.message` for a non-`ApiError`; and `error.status` was lost entirely, so
 * no caller could branch on the code. The 413 and 415 branches in
 * `toUserMessage` were unreachable for the upload route that is the only thing
 * that can produce them.
 *
 * These tests pin the contract rather than the implementation: an `ApiError`
 * with a real status, the server's `detail` preferred when present, and the
 * mapped copy when it is not.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createTextBook, previewVoice, uploadDocument, uploadVoice } from '$lib/api'
import { ApiError, toUserMessage } from '$lib/utils/errors'

function mockErrorResponse(status: number, statusText: string, body: unknown = {}) {
  ;(global.fetch as any).mockResolvedValueOnce({
    ok: false,
    status,
    statusText,
    json: async () => body,
  })
}

const ENDPOINTS: Array<[string, () => Promise<unknown>]> = [
  ['uploadDocument', () => uploadDocument(new File(['x'], 'book.pdf'))],
  ['uploadVoice', () => uploadVoice(new File(['x'], 'voice.pt'))],
  ['previewVoice', () => previewVoice('af_heart')],
  ['createTextBook', () => createTextBook('Hello there.')],
]

describe('every endpoint reports failures as ApiError', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = vi.fn()
  })

  for (const [name, call] of ENDPOINTS) {
    it(`${name} rejects with an ApiError that keeps the status`, async () => {
      mockErrorResponse(500, 'Internal Server Error')

      const error = await call().catch((e: unknown) => e)

      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(500)
    })

    it(`${name} prefers the server's own detail`, async () => {
      mockErrorResponse(400, 'Bad Request', { detail: 'Unsupported file type' })

      const error = await call().catch((e: unknown) => e)

      expect((error as Error).message).toBe('Unsupported file type')
    })
  }

  it('maps a proxy 413 to the copy the user should see', async () => {
    // The backend itself never returns 413 (the status codes it raises are 400,
    // 403, 404, 409, 500 and 503) — a reverse proxy's body-size limit does, and
    // that response is HTML, so there is no `detail` to prefer and the status is
    // all the client has to go on.
    mockErrorResponse(413, 'Payload Too Large', null)

    const error = await uploadDocument(new File(['x'], 'huge.pdf')).catch((e: unknown) => e)

    expect(toUserMessage(error)).toBe('That file is too large')
  })

  it('still reports a dead backend as unreachable, not as a status line', async () => {
    ;(global.fetch as any).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    const error = await uploadDocument(new File(['x'], 'book.pdf')).catch((e: unknown) => e)

    expect(toUserMessage(error)).toBe('Could not connect to the backend')
  })
})
