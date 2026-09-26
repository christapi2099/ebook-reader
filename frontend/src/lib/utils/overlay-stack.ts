/**
 * Ordered registry of open overlay layers (dialogs, drawers, panels).
 *
 * Layers register when they mount and unregister when they unmount, so the
 * registration order *is* the stacking order: whatever opened last sits on top.
 * Escape is dispatched to the topmost layer only — a dialog opened over a panel
 * must not close the panel underneath it.
 */

type CloseHandler = () => void

const layers: CloseHandler[] = []
let listening = false

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  const top = layers[layers.length - 1]
  if (!top) return
  event.preventDefault()
  event.stopPropagation()
  top()
}

/** Register an open layer. Returns the unregister function for teardown. */
export function registerOverlay(close: CloseHandler): () => void {
  layers.push(close)

  if (!listening && typeof window !== 'undefined') {
    window.addEventListener('keydown', handleKeydown)
    listening = true
  }

  return () => {
    const index = layers.indexOf(close)
    if (index !== -1) layers.splice(index, 1)
    if (layers.length === 0 && listening) {
      window.removeEventListener('keydown', handleKeydown)
      listening = false
    }
  }
}
