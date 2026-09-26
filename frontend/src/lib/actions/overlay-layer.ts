import { registerOverlay } from '$lib/utils/overlay-stack'

export interface OverlayLayerOptions {
  /** Called when the layer should close (Escape, or a backdrop click). */
  onClose: () => void
  /** Selector for the element that receives focus when the layer opens. */
  initialFocus?: string
  /**
   * Modal layers keep Tab inside the layer and hand focus back to the element
   * that opened them. Non-modal panels (the inline search bar) opt out.
   */
  modal?: boolean
  /** Close when the backdrop — the node itself, outside its children — is clicked. */
  closeOnBackdrop?: boolean
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    element => element.getClientRects().length > 0
  )
}

/**
 * Overlay-layer behaviour, applied with `use:` to the layer's outermost element:
 *
 * - moves focus into the layer when it opens,
 * - traps Tab inside a modal layer,
 * - registers with the overlay stack so Escape closes only the topmost layer,
 * - restores focus to the trigger when the layer closes.
 *
 * ARIA (`role="dialog"`, `aria-modal`, `aria-labelledby`) stays in markup where
 * it is greppable; this only does the behaviour.
 */
export function overlayLayer(node: HTMLElement, options: OverlayLayerOptions) {
  let opts = options

  // Captured before focus moves, so it is still whatever opened this layer.
  const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null

  function handleKeydown(event: KeyboardEvent) {
    if (!opts.modal || event.key !== 'Tab') return
    const focusable = focusableWithin(node)
    if (focusable.length === 0) {
      event.preventDefault()
      node.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const active = document.activeElement
    const outside = !node.contains(active)
    if (event.shiftKey && (active === first || outside)) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (active === last || outside)) {
      event.preventDefault()
      first.focus()
    }
  }

  function handleClick(event: MouseEvent) {
    if (opts.closeOnBackdrop && event.target === node) opts.onClose()
  }

  node.addEventListener('keydown', handleKeydown)
  node.addEventListener('click', handleClick)

  const unregister = registerOverlay(() => opts.onClose())

  const requested = opts.initialFocus ? node.querySelector<HTMLElement>(opts.initialFocus) : null
  const target = requested ?? focusableWithin(node)[0] ?? node
  target.focus()

  return {
    update(next: OverlayLayerOptions) {
      opts = next
    },
    destroy() {
      node.removeEventListener('keydown', handleKeydown)
      node.removeEventListener('click', handleClick)
      unregister()
      if (trigger && document.contains(trigger)) trigger.focus()
    },
  }
}
