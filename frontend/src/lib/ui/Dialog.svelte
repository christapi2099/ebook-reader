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
   * The panel is deliberately left to the caller. The panels differ
   * meaningfully — one is a `<form>`, one is a full-height list, one is a
   * right-hand sheet — and wrapping them in a shared `<div>` would change the
   * rendered DOM of every dialog for no gain. Only the part that was genuinely
   * identical moved in here.
   *
   * `role="dialog"` and the action stay on this element, not on the panel:
   * backdrop-close works by comparing `event.target` to the node the action is
   * applied to, and the tests click that node directly.
   *
   * This always renders `role="dialog"`. Anything that is not one — the inline
   * search bar is `role="search"` with no scrim — must keep using the
   * `overlayLayer` action directly rather than coming through here, or it would
   * be announced as a dialog.
   */
  // The prop names the flex alignment it applies, not a component kind:
  // DESIGN.md's `Sheet` is a responsive draggable panel with snap points, which
  // this is not, and building it for one consumer would be speculative.
  type Layout = 'centered' | 'stretch'

  const LAYOUT_CLASSES: Record<Layout, string> = {
    centered: 'items-center justify-center',
    // Fills the height and lets the panel align itself, e.g. with `ml-auto`.
    stretch: 'items-stretch',
  }

  let {
    titleId,
    onClose,
    layout = 'centered',
    closeOnBackdrop = false,
    initialFocus,
    modal = true,
    class: className = '',
    children,
  }: {
    /** Id of the element naming this dialog, for `aria-labelledby`. */
    titleId: string
    onClose: () => void
    layout?: Layout
    closeOnBackdrop?: boolean
    /** Selector for the element that receives focus when the dialog opens. */
    initialFocus?: string
    modal?: boolean
    class?: string
    children: Snippet
  } = $props()
</script>

<div
  class="fixed inset-0 z-dialog flex bg-overlay {LAYOUT_CLASSES[layout]} {className}"
  role="dialog"
  aria-modal={modal ? 'true' : undefined}
  aria-labelledby={titleId}
  tabindex="-1"
  use:overlayLayer={{ onClose, closeOnBackdrop, initialFocus, modal }}
>
  {@render children()}
</div>
