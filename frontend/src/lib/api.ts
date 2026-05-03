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

export const API_BASE = 'http://localhost:8000'

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

export interface Voice {
  id: string
  name: string
  lang: string
  gender: string
  quality: string
  built_in: boolean
}

export async function getVoices(): Promise<Voice[]> {
  return fetchApi<Voice[]>('/voices')
}

export async function uploadVoice(file: File): Promise<{ id: string; path: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE}/voices/upload`, { method: 'POST', body: formData })
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  return response.json()
}

export async function deleteVoice(voiceId: string): Promise<void> {
  await fetchApi<void>(`/voices/${voiceId}`, { method: 'DELETE' })
}

export async function previewVoice(voiceId: string): Promise<ArrayBuffer> {
  const response = await fetch(`${API_BASE}/voices/preview/${voiceId}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  return response.arrayBuffer()
}

export async function deleteBook(bookId: string): Promise<void> {
  await fetchApi<void>(`/library/${bookId}`, { method: 'DELETE' })
}

export async function exportMP3(bookId: string, voice: string, speed: number): Promise<{ export_id: number }> {
  return fetchApi<{ export_id: number }>('/mp3/export', {
    method: 'POST',
    body: JSON.stringify({ book_id: bookId, voice, speed }),
  })
}

export interface ExportStatus {
  status: string
  progress: number
  file_size: number | null
  error_message: string | null
}

export interface ExportItem {
  id: number
  book_id: string
  book_title: string
  voice: string
  speed: number
  status: string
  progress: number
  file_size: number | null
  error_message: string | null
  created_at: string
}

export async function getExports(): Promise<ExportItem[]> {
  return fetchApi<ExportItem[]>('/mp3/exports')
}

export async function getExportStatus(exportId: number): Promise<ExportStatus> {
  return fetchApi(`/mp3/exports/${exportId}/status`)
}

export async function deleteExport(exportId: number): Promise<void> {
  await fetchApi<void>(`/mp3/exports/${exportId}`, { method: 'DELETE' })
}

export interface Bookmark {
  id: number
  book_id: string
  sentence_index: number
  page: number
  label: string
  created_at: string
}

export async function createBookmark(bookId: string, sentenceIndex: number, label: string): Promise<Bookmark> {
  return fetchApi<Bookmark>('/bookmarks', {
    method: 'POST',
    body: JSON.stringify({ book_id: bookId, sentence_index: sentenceIndex, label }),
  })
}

export async function getBookmarks(bookId: string): Promise<Bookmark[]> {
  return fetchApi<Bookmark[]>(`/bookmarks/${bookId}`)
}

export async function deleteBookmark(bookmarkId: number): Promise<void> {
  await fetchApi<void>(`/bookmarks/${bookmarkId}`, { method: 'DELETE' })
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

export interface UserSettings {
  last_book_id: string | null
  last_sentence_index: number
}

export async function getUserSettings(): Promise<UserSettings> {
  return fetchApi<UserSettings>('/user/settings')
}

export async function updateUserSettings(settings: {
  last_book_id?: string | null
  last_sentence_index?: number
}): Promise<{ ok: boolean }> {
  return fetchApi('/user/settings', {
    method: 'POST',
    body: JSON.stringify(settings),
  })
}

// Text book API functions

export async function createTextBook(text: string, title?: string): Promise<{ book_id: string; sentence_count: number; already_existed: boolean }> {
  const body = title ? { text, title } : { text }
  const response = await fetch(`${API_BASE}/documents/text`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  return response.json()
}

export async function persistTextBook(bookId: string, title?: string): Promise<{ ok: boolean }> {
  return fetchApi(`/documents/text/${bookId}`, {
    method: 'PATCH',
    body: title ? JSON.stringify({ title }) : undefined,
  })
}

export async function getBook(bookId: string): Promise<Book> {
  return fetchApi<Book>(`/library/${bookId}`)
}

export class TTSSocket {
  private ws: WebSocket | null = null
  private bookId: string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5

  onAudioChunk: (bytes: ArrayBuffer) => void = () => {}
  onSentenceStart: (index: number, sessionId: number) => void = () => {}
  onSentenceEnd: (index: number, durationMs: number, sessionId: number) => void = () => {}
  onComplete: (sessionId: number) => void = () => {}

  constructor(bookId: string) {
    this.bookId = bookId
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const wsUrl = `${API_BASE.replace(/^http/, 'ws')}/ws/tts/${this.bookId}`
    this.ws = new WebSocket(wsUrl)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onopen = () => { this.reconnectAttempts = 0 }
    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.onAudioChunk(event.data)
      } else {
        try {
          const msg = JSON.parse(event.data)
          const sid = typeof msg.session_id === 'number' ? msg.session_id : 0
          if (msg.type === 'sentence_start') this.onSentenceStart(msg.index, sid)
          else if (msg.type === 'sentence_end') this.onSentenceEnd(msg.index, msg.duration_ms, sid)
          else if (msg.type === 'complete') this.onComplete(sid)
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

  play(fromIndex: number, voice = 'af_heart', speed = 1.0, sessionId = 0): void {
    this._send({ action: 'play', from_index: fromIndex, voice, speed, session_id: sessionId })
  }

  seek(toIndex: number, voice = 'af_heart', speed = 1.0, sessionId = 0): void {
    this._send({ action: 'seek', to_index: toIndex, voice, speed, session_id: sessionId })
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
