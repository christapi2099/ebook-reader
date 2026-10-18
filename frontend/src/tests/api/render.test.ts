import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  API_BASE, NotReadyError, getRenderManifest, getSentences, renderAudioUrl,
  reprocessBook, cancelRender, setBookVoice, getProcessing,
} from '$lib/api'
import { ApiError } from '$lib/utils/errors'

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status < 400, status, statusText: 'x', json: async () => body,
  })
}

describe('render api', () => {
  let original: typeof fetch
  beforeEach(() => { original = globalThis.fetch })
  afterEach(() => { globalThis.fetch = original })

  it('builds the immutable tier url', () => {
    expect(renderAudioUrl('b-1', 3, '1.5x')).toBe(`${API_BASE}/api/renders/b-1/3/audio_1.5x.opus`)
  })

  it('getSentences pins the generation when given', async () => {
    const f = respond(200, []); globalThis.fetch = f as never
    await getSentences('b', 4)
    expect(f.mock.calls[0][0]).toBe(`${API_BASE}/documents/b/sentences?generation=4`)
    await getSentences('b')
    expect(f.mock.calls[1][0]).toBe(`${API_BASE}/documents/b/sentences`)
  })

  it('maps 409 on sentences and manifest to NotReadyError', async () => {
    globalThis.fetch = respond(409, { detail: 'not ready' }) as never
    await expect(getSentences('b')).rejects.toBeInstanceOf(NotReadyError)
    await expect(getRenderManifest('b')).rejects.toMatchObject({ name: 'NotReadyError', bookId: 'b' })
  })

  it('keeps other failures as ApiError', async () => {
    globalThis.fetch = respond(500, {}) as never
    await expect(getSentences('b')).rejects.toBeInstanceOf(ApiError)
  })

  it('reprocess, cancel, voice and processing hit the contract endpoints', async () => {
    const f = respond(200, {}); globalThis.fetch = f as never
    await reprocessBook('b')
    await cancelRender('b')
    await setBookVoice('b', 'af_bella')
    await getProcessing('b')
    expect(f.mock.calls[0]).toEqual([`${API_BASE}/library/b/reprocess`, expect.objectContaining({ method: 'POST' })])
    expect(f.mock.calls[1][0]).toBe(`${API_BASE}/library/b/reprocess?cancel=true`)
    expect(f.mock.calls[2][1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ voice: 'af_bella' }) })
    expect(f.mock.calls[3][0]).toBe(`${API_BASE}/library/b/processing`)
  })
})
