import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { getCapabilities, getEngineState, setEngine, TTSSocket } from '$lib/api'

const ENGINE_STATE = {
  selected: 'cpu',
  active: 'cpu',
  switching: false,
  phase: 'idle',
  options: [
    { id: 'cpu', label: 'This device · CPU', available: true, reason: null },
    { id: 'gpu', label: 'This device · GPU', available: false, reason: 'No CUDA GPU detected' },
  ],
}

describe('system capabilities + engine API', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  it('reads the capability probe', async () => {
    const payload = { active_backend: 'local', synthesis_available: true, sample_rate: 24000 }
    ;(global.fetch as any).mockResolvedValueOnce({ ok: true, json: async () => payload })

    const result = await getCapabilities()

    expect(result).toEqual(payload)
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/system/capabilities',
      { headers: { 'Content-Type': 'application/json' } },
    )
  })

  it('reads the live engine state with its per-engine verdicts', async () => {
    ;(global.fetch as any).mockResolvedValueOnce({ ok: true, json: async () => ENGINE_STATE })

    const result = await getEngineState()

    expect(result.options[1].reason).toBe('No CUDA GPU detected')
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/system/engine',
      { headers: { 'Content-Type': 'application/json' } },
    )
  })

  it('switches the engine by id', async () => {
    ;(global.fetch as any).mockResolvedValueOnce({ ok: true, json: async () => ENGINE_STATE })

    await setEngine('gpu')

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/system/engine',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engine: 'gpu' }),
      },
    )
  })

  it('surfaces the probe’s own reason when a switch is refused', async () => {
    ;(global.fetch as any).mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({ detail: 'No CUDA GPU detected' }),
    })

    await expect(setEngine('gpu')).rejects.toThrow('No CUDA GPU detected')
  })
})

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.OPEN
  binaryType = ''
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  sent: string[] = []

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
  }

  /** Deliver a server message to the client's parser. */
  deliver(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
}

describe('TTSSocket message parsing', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function connectedSocket() {
    const socket = new TTSSocket('book-1')
    socket.connect()
    return { socket, ws: FakeWebSocket.instances.at(-1)! }
  }

  it('reports a refused playback rate with its session id', () => {
    const { socket, ws } = connectedSocket()
    const onSpeedUnavailable = vi.fn()
    socket.onSpeedUnavailable = onSpeedUnavailable

    ws.deliver({ type: 'speed_unavailable', requested_speed: 1.5, effective_speed: 1.0, session_id: 3 })

    expect(onSpeedUnavailable).toHaveBeenCalledWith(1.5, 1.0, 3)
  })

  it('does not crash when nothing is listening for the notice', () => {
    const { ws } = connectedSocket()

    expect(() =>
      ws.deliver({ type: 'speed_unavailable', requested_speed: 1.5, effective_speed: 1.0, session_id: 1 }),
    ).not.toThrow()
  })

  it('ignores a notice whose rates are not numbers', () => {
    const { socket, ws } = connectedSocket()
    const onSpeedUnavailable = vi.fn()
    socket.onSpeedUnavailable = onSpeedUnavailable

    ws.deliver({ type: 'speed_unavailable', requested_speed: 'fast', effective_speed: null, session_id: 1 })

    expect(onSpeedUnavailable).not.toHaveBeenCalled()
  })

  it('still parses sentence_end beside the new message', () => {
    const { socket, ws } = connectedSocket()
    const onSentenceEnd = vi.fn()
    socket.onSentenceEnd = onSentenceEnd

    ws.deliver({
      type: 'sentence_end',
      index: 4,
      duration_ms: 1200,
      word_timestamps: [{ word: 'hi', start: 0, end: 0.4 }],
      session_id: 2,
    })

    expect(onSentenceEnd).toHaveBeenCalledWith(4, 1200, 2, [{ word: 'hi', start: 0, end: 0.4 }])
  })
})
