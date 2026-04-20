export interface Sentence {
  index: number
  text: string
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
  filtered: boolean
}

export interface Book {
  id: string
  title: string
  author: string | null
  file_type: string
  page_count: number
}

const API_BASE = 'http://localhost:8000'

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  return response.json() as Promise<T>
}

export async function uploadDocument(
  file: File
): Promise<{ book_id: string; sentence_count: number; already_existed: boolean }> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE}/documents/upload`, { method: 'POST', body: formData })
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  return response.json()
}

export async function getSentences(bookId: string): Promise<Sentence[]> {
  return fetchApi<Sentence[]>(`/documents/${bookId}/sentences`)
}

export async function getLibrary(): Promise<Book[]> {
  return fetchApi<Book[]>('/library')
}

export async function saveProgress(bookId: string, sentenceIndex: number): Promise<void> {
  await fetchApi<void>(`/library/${bookId}/progress`, {
    method: 'POST',
    body: JSON.stringify({ sentence_index: sentenceIndex }),
  })
}

export async function getProgress(bookId: string): Promise<number> {
  const res = await fetchApi<{ sentence_index: number }>(`/library/${bookId}/progress`)
  return res.sentence_index
}

export class TTSSocket {
  private ws: WebSocket | null = null
  private bookId: string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5

  onAudioChunk: (bytes: ArrayBuffer) => void = () => {}
  onSentenceStart: (index: number) => void = () => {}
  onSentenceEnd: (index: number, durationMs: number) => void = () => {}
  onComplete: () => void = () => {}

  constructor(bookId: string) {
    this.bookId = bookId
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const wsUrl = `ws://localhost:8000/ws/tts/${this.bookId}`
    this.ws = new WebSocket(wsUrl)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onopen = () => { this.reconnectAttempts = 0 }
    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.onAudioChunk(event.data)
      } else {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'sentence_start') this.onSentenceStart(msg.index)
          else if (msg.type === 'sentence_end') this.onSentenceEnd(msg.index, msg.duration_ms)
          else if (msg.type === 'complete') this.onComplete()
        } catch {}
      }
    }
    this.ws.onclose = () => this._attemptReconnect()
  }

  private _attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return
    this.reconnectAttempts++
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 10000)
    setTimeout(() => {
      if (this.ws?.readyState !== WebSocket.OPEN) this.connect()
    }, delay)
  }

  play(fromIndex: number, voice = 'af_heart'): void {
    this._send({ action: 'play', from_index: fromIndex, voice })
  }

  seek(toIndex: number): void {
    this._send({ action: 'seek', to_index: toIndex })
  }

  pause(): void {
    this._send({ action: 'pause' })
  }

  private _send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(data))
  }

  disconnect(): void {
    this.reconnectAttempts = this.maxReconnectAttempts
    this.ws?.close(1000, 'Client disconnected')
    this.ws = null
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}
