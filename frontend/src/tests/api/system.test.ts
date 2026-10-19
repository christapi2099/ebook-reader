import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { getCapabilities, getEngineState, getKokoroOnnxInstall, installKokoroOnnx, getGpus, refreshGpus, selectGpu, setEngine, TTSSocket } from '$lib/api'

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

describe('Kokoro ONNX install API', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  it('starts the download with a POST and reads progress with a GET', async () => {
    const running = { status: 'running', file: 'kokoro-v1.0.onnx', downloaded: 5, total: 10, error: null }
    ;(global.fetch as any).mockResolvedValue({ ok: true, json: async () => running })

    expect(await installKokoroOnnx()).toEqual(running)
    expect(global.fetch).toHaveBeenLastCalledWith(
      'http://localhost:8000/api/system/engine/kokoro-onnx/install',
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
    )
    await getKokoroOnnxInstall()
    expect(global.fetch).toHaveBeenLastCalledWith(
      'http://localhost:8000/api/system/engine/kokoro-onnx/install',
      { headers: { 'Content-Type': 'application/json' } },
    )
  })
})

describe('GPU inventory API', () => {
  const INVENTORY = {
    devices: [], selected: null, default: null, needs_choice: false, torch_build: 'cpu', state: 'ready',
  }

  beforeEach(() => {
    global.fetch = vi.fn()
    ;(global.fetch as any).mockResolvedValue({ ok: true, json: async () => INVENTORY })
  })

  it('reads the inventory', async () => {
    expect(await getGpus()).toEqual(INVENTORY)
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/system/gpus',
      { headers: { 'Content-Type': 'application/json' } },
    )
  })

  it('asks for a re-scan with POST', async () => {
    await refreshGpus()
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/system/gpus/refresh',
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
    )
  })

  it('selects a device, or null to clear', async () => {
    await selectGpu('pci:0000:03:00.0')
    await selectGpu(null)
    const calls = (global.fetch as any).mock.calls
    expect(calls[0]).toEqual(['http://localhost:8000/api/system/gpus', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: 'pci:0000:03:00.0' }),
    }])
    expect(JSON.parse(calls[1][1].body)).toEqual({ device_id: null })
  })

  it('surfaces the server reason when a device is refused', async () => {
    ;(global.fetch as any).mockResolvedValueOnce({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({ detail: 'needs the ROCm torch environment' }),
    })
    await expect(selectGpu('pci:x')).rejects.toThrow('needs the ROCm torch environment')
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
