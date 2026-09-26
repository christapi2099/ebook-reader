import { ApiError } from '$lib/utils/errors'

export interface WordBbox {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface Sentence {
  index: number
  text: string
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
  filtered: boolean
  words?: WordBbox[]
  chapter: number
  chapter_title?: string
}

export interface Book {
  id: string
  title: string
  author: string | null
  file_type: string
  page_count: number
  /** `null` when the book is not filed in any folder. */
  folder_id: number | null
  /**
   * Sentences in the book, counted by the server. This is the only usable
   * denominator for a progress bar: `page_count` means PDF pages, a sentence
   * count or a derived guess depending on `file_type`.
   */
  sentence_count?: number
  /**
   * Where the reader is parked: `null` when the book was never started, `0` when
   * it was started and left on the first sentence. The two used to be the same
   * value, which is why an unread book and a barely-started one looked alike.
   */
  sentence_index?: number | null
}

/** Longest folder name the backend accepts (`FOLDER_NAME_MAX_LENGTH`). */
export const FOLDER_NAME_MAX_LENGTH = 60

export interface Folder {
  id: number
  name: string
  created_at: string
  /** Counted by the server, so the tile never has to guess. */
  book_count: number
}

export const API_BASE = 'http://localhost:8000'

/**
 * Turn a failed response into an `ApiError` carrying the server's own
 * explanation. FastAPI puts the human-readable reason in `detail` for 400/409,
 * so the UI can show "A folder with that name already exists" instead of a
 * status code. A missing or non-JSON body leaves the generic message in place.
 */
async function toApiError(response: Response): Promise<ApiError> {
  let detail: string | undefined
  try {
    const body = await response.json()
    if (body && typeof body.detail === 'string' && body.detail.trim()) detail = body.detail
  } catch {
    // No JSON body — fall back to the status line.
  }
  return new ApiError(response.status, response.statusText, detail)
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) throw await toApiError(response)
  return response.json() as Promise<T>
}

export async function uploadDocument(
  file: File
): Promise<{ book_id: string; sentence_count: number; already_existed: boolean }> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE}/documents/upload`, { method: 'POST', body: formData })
  if (!response.ok) throw await toApiError(response)
  return response.json()
}

export async function getSentences(bookId: string): Promise<Sentence[]> {
  return fetchApi<Sentence[]>(`/documents/${bookId}/sentences`)
}

/**
 * Absolute URL of a book's uploaded PDF. PDF.js fetches this itself via
 * `getDocument(url)`, so only the URL is built here — `api.ts` remains the one
 * place that knows the backend origin.
 */
export function getPdfUrl(bookId: string): string {
  return `${API_BASE}/uploads/${encodeURIComponent(bookId)}.pdf`
}

export async function getLibrary(): Promise<Book[]> {
  return fetchApi<Book[]>('/library')
}

// Folders — user-created groupings of library books.

export async function getFolders(): Promise<Folder[]> {
  return fetchApi<Folder[]>('/folders')
}

/** Rejects with the server's `detail` for a blank, duplicate or too-long name. */
export async function createFolder(name: string): Promise<Folder> {
  return fetchApi<Folder>('/folders', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function renameFolder(folderId: number, name: string): Promise<Folder> {
  return fetchApi<Folder>(`/folders/${folderId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

/** Deletes the folder and unfiles its books — the books themselves are kept. */
export async function deleteFolder(folderId: number): Promise<{ ok: boolean; unfiled_books: number }> {
  return fetchApi<{ ok: boolean; unfiled_books: number }>(`/folders/${folderId}`, { method: 'DELETE' })
}

/** Files a book, or clears its folder with `folderId = null`. */
export async function setBookFolder(
  bookId: string,
  folderId: number | null,
): Promise<{ ok: boolean; folder_id: number | null }> {
  return fetchApi<{ ok: boolean; folder_id: number | null }>(`/library/${bookId}/folder`, {
    method: 'POST',
    body: JSON.stringify({ folder_id: folderId }),
  })
}

/**
 * Set a book's title and/or author. Keys left out are not changed; `author:
 * null` clears the byline, and so does an empty string, which the server
 * normalises rather than storing.
 *
 * Rejects with the server's `detail` for a blank or too-long title, or for a
 * body that asks for nothing.
 */
export async function updateBook(
  bookId: string,
  changes: { title?: string; author?: string | null },
): Promise<Book> {
  return fetchApi<Book>(`/library/${bookId}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })
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
  if (!response.ok) throw await toApiError(response)
  return response.json()
}

export async function deleteVoice(voiceId: string): Promise<void> {
  await fetchApi<void>(`/voices/${voiceId}`, { method: 'DELETE' })
}

export async function previewVoice(voiceId: string): Promise<ArrayBuffer> {
  const response = await fetch(`${API_BASE}/voices/preview/${voiceId}`)
  if (!response.ok) throw await toApiError(response)
  return response.arrayBuffer()
}

/**
 * The sentence the preview endpoint synthesises, verbatim from
 * `backend/routers/voices.py`. The preview dock shows it so the caption
 * describes the audio that is actually playing instead of a made-up line.
 */
export const VOICE_PREVIEW_SAMPLE_TEXT = 'The quick brown fox jumps over the lazy dog.'

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

/**
 * Persist the position without waiting for a reply, for the one moment a normal
 * request cannot be used: the page is being unloaded and an ordinary fetch is
 * cancelled along with it.
 *
 * `navigator.sendBeacon` is the request a browser guarantees to deliver during
 * unload. A `keepalive` fetch is the fallback where `sendBeacon` is missing, and
 * it is deliberately not awaited. Returns whether the send was handed to the
 * browser — `false` leaves the caller to fall back to the normal path.
 */
export function saveProgressBeacon(bookId: string, sentenceIndex: number): boolean {
  const url = `${API_BASE}/library/${encodeURIComponent(bookId)}/progress`
  const body = JSON.stringify({ sentence_index: sentenceIndex })
  if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
    try {
      // A `true` here means the browser took ownership of the request; a `false`
      // means it refused (queue full, or the body was rejected) and nothing was
      // sent, so fall through to the fetch rather than losing the position.
      if (navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))) {
        return true
      }
    } catch {
      // Some engines throw instead of returning false for an oversized body.
    }
  }
  // `keepalive` is what lets this outlive the document; it is deliberately not
  // awaited, and a rejection here is unreportable by design.
  void fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {})
  return true
}

export async function getProgress(bookId: string): Promise<number> {
  const res = await fetchApi<{ sentence_index: number }>(`/library/${bookId}/progress`)
  return res.sentence_index
}

export interface UserSettings {
  last_book_id: string | null
  last_sentence_index: number
  highlight_enabled?: boolean
}

export interface WordTimestamp {
  word: string
  start: number
  end: number
}

export async function getUserSettings(): Promise<UserSettings> {
  return fetchApi<UserSettings>('/user/settings')
}

export async function updateUserSettings(settings: {
  last_book_id?: string | null
  last_sentence_index?: number
  highlight_enabled?: boolean
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
  if (!response.ok) throw await toApiError(response)
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

// System — what the backend can actually do, and which engine is live.

export interface EngineOption {
  /** `"cpu" | "gpu" | "modal"` — the id `setEngine` accepts. */
  id: string
  label: string
  /** From a real probe: torch import for cpu, CUDA for gpu, Modal for modal. */
  available: boolean
  /** Why it is unavailable. `null` when it is available. */
  reason: string | null
}

export interface EngineState {
  /** What the user last chose; `null` means the env default decided. */
  selected: string | null
  /** The engine that is live right now; `null` before the first build. */
  active: string | null
  switching: boolean
  phase: string
  options: EngineOption[]
}

export interface LocalCapabilities {
  torch_version: string | null
  torch_cuda_version: string | null
  cuda_available: boolean
  cuda_device_count: number
  gpu_name: string | null
  /** Device the live local pipeline was built on; `null` when it is not local. */
  device_in_use: string | null
  model_repo: string | null
  error: string | null
}

export interface RemoteCapabilities {
  app_name?: string
  function_name?: string
  transport?: string
  /** Ask-time GPU preference for the deploy, not evidence of what is deployed. */
  gpu_preference?: string | null
  timeout_s?: number
  health_url?: string | null
  configured: boolean
  resolved_transport: string | null
  credentials_present: boolean
  reachable: boolean
  error: string | null
  /** The transport that would be used if the remote backend were selected. */
  would_use: string | null
}

export interface SystemCapabilities {
  active_backend: string
  requested_backend: string
  synthesis_available: boolean
  sample_rate: number
  local: LocalCapabilities
  remote: RemoteCapabilities
  errors: { startup: string | null; remote: string | null }
}

/** Live probe of the device, the torch build and remote reachability. */
export async function getCapabilities(): Promise<SystemCapabilities> {
  return fetchApi<SystemCapabilities>('/api/system/capabilities')
}

/** Which engine is live, which one was chosen, and what each one needs. */
export async function getEngineState(): Promise<EngineState> {
  return fetchApi<EngineState>('/api/system/engine')
}

/**
 * Switch the live synthesis engine. Genuinely slow — a local model load or a
 * Modal container boot — so callers must show a pending state. Rejects with the
 * server's `detail` (409 when the probe says this machine cannot run it).
 */
export async function setEngine(engineId: string): Promise<EngineState> {
  return fetchApi<EngineState>('/api/system/engine', {
    method: 'POST',
    body: JSON.stringify({ engine: engineId }),
  })
}

export class TTSSocket {
  private ws: WebSocket | null = null
  private bookId: string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private _pendingMessage: unknown = null

  onAudioChunk: (bytes: ArrayBuffer) => void = () => {}
  onSentenceStart: (index: number, sessionId: number) => void = () => {}
  onSentenceEnd: (index: number, durationMs: number, sessionId: number, wordTimestamps?: WordTimestamp[]) => void = () => {}
  onComplete: (sessionId: number) => void = () => {}
  /**
   * The engine cannot render at the requested rate, so it is producing
   * `effectiveSpeed` instead. Sent at most once per connection, before that
   * sentence's `sentence_end`.
   */
  onSpeedUnavailable?: (requestedSpeed: number, effectiveSpeed: number, sessionId: number) => void

  constructor(bookId: string) {
    this.bookId = bookId
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const wsUrl = `${API_BASE.replace(/^http/, 'ws')}/ws/tts/${this.bookId}`
    this.ws = new WebSocket(wsUrl)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      if (this._pendingMessage !== null) {
        this.ws!.send(JSON.stringify(this._pendingMessage))
        this._pendingMessage = null
      }
    }
    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.onAudioChunk(event.data)
      } else {
        try {
          const msg = JSON.parse(event.data)
          const sid = typeof msg.session_id === 'number' ? msg.session_id : 0
          if (msg.type === 'sentence_start') this.onSentenceStart(msg.index, sid)
          else if (msg.type === 'sentence_end') this.onSentenceEnd(msg.index, msg.duration_ms, sid, msg.word_timestamps)
          else if (msg.type === 'speed_unavailable') {
            // Only report a rate the engine actually named; a malformed message
            // must not silently rewrite the UI's idea of the playback speed.
            if (typeof msg.requested_speed === 'number' && typeof msg.effective_speed === 'number') {
              this.onSpeedUnavailable?.(msg.requested_speed, msg.effective_speed, sid)
            }
          }
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

  prefetchSpeed(fromIndex: number, voice = 'af_heart', speed = 1.0): void {
    this._send({ action: 'prefetch_speed', from_index: fromIndex, voice, speed })
  }

  pause(): void {
    this._send({ action: 'pause' })
  }

  private _send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    } else {
      this._pendingMessage = data
    }
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
