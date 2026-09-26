import { writable } from 'svelte/store'

export type ToastTone = 'info' | 'success' | 'warning' | 'danger'

export interface ToastAction {
  label: string
  onClick: () => void
}

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  message?: string
  action?: ToastAction
  /** ms before auto-dismiss; 0 = sticky */
  duration: number
}

const MAX_TOASTS = 3
const DEFAULT_DURATION = 5000

let nextId = 1

function createToastStore() {
  const { subscribe, update } = writable<Toast[]>([])
  const timers = new Map<number, ReturnType<typeof setTimeout>>()

  function clearTimer(id: number) {
    const timer = timers.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.delete(id)
    }
  }

  function dismiss(id: number) {
    clearTimer(id)
    update(toasts => toasts.filter(t => t.id !== id))
  }

  function push(options: {
    tone?: ToastTone
    title: string
    message?: string
    action?: ToastAction
    duration?: number
  }): number {
    const id = nextId++
    const toast: Toast = {
      id,
      tone: options.tone ?? 'info',
      title: options.title,
      message: options.message,
      action: options.action,
      duration: options.duration ?? DEFAULT_DURATION,
    }
    update(toasts => {
      const next = [...toasts, toast]
      while (next.length > MAX_TOASTS) {
        const evicted = next.shift()
        if (evicted) clearTimer(evicted.id)
      }
      return next
    })
    if (toast.duration > 0 && typeof setTimeout === 'function') {
      timers.set(
        id,
        setTimeout(() => dismiss(id), toast.duration),
      )
    }
    return id
  }

  return { subscribe, push, dismiss }
}

export const toastStore = createToastStore()
