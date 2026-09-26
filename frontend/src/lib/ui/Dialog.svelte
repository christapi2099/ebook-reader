<script lang="ts">
  import type { Snippet } from 'svelte'
  import { overlayLayer } from '$lib/actions/overlay-layer'

  /**
   * The scrim, the ARIA and the focus/Escape behaviour every modal shares.
   *
   * Six dialogs had grown their own copy of the same outermost element, and the
   * copies had drifted: five used a hardcoded `z-50` and `bg-black/50` while
   * Settings used the theme tokens `z-overlay` (40) and `bg-overlay`. The
   * hardcoded ones ignored the theme, and Settings sat *below* every other
   * dialog despite being the same kind of thing. Both problems are structural,
   * so they are fixed once, here.
   *
   * The panel is deliberately left to the caller. The six panels differ
   * meaningfully — one is a `<form>`, one is a full-height list, one is a
   * bottom-anchored sheet — and wrapping them in a shared `<div>` would change
   * the rendered DOM of every dialog for no gain. Only the part that was
   * genuinely identical moved in here.
   *
   * `role="dialog"` and the action stay on this element, not on the panel:
   * backdrop-close works by comparing `event.target` to the node the action is
   * applied to, and the tests click that node directly.
   */
  let {
    titleId,
    onClose,
    closeOnBackdrop = false,
    initialFocus,
    modal = true,
    class: className = '',
    children,
  }: {
    /** Id of the element naming this dialog, for `aria-labelledby`. */
    titleId: string
    onClose: () => void
    closeOnBackdrop?: boolean
    /** Selector for the element that receives focus when the dialog opens. */
    initialFocus?: string
    modal?: boolean
    class?: string
    children: Snippet
  } = $props()
</script>

<div
  class="fixed inset-0 z-dialog flex items-center justify-center bg-overlay {className}"
  role="dialog"
  aria-modal={modal ? 'true' : undefined}
  aria-labelledby={titleId}
  tabindex="-1"
  use:overlayLayer={{ onClose, closeOnBackdrop, initialFocus, modal }}
>
  {@render children()}
</div>
