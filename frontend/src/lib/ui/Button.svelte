<script lang="ts">
  import type { HTMLButtonAttributes } from 'svelte/elements'
  import type { Snippet } from 'svelte'

  /**
   * The app's one button.
   *
   * Before this existed the accent-button class string was written out at 17
   * call sites across two generations: older files used raw `bg-blue-500` /
   * `bg-blue-600` and newer ones the semantic `bg-accent`. Those are different
   * colours (`#3b82f6` against `#5146d9`), and only the semantic one follows the
   * theme — so in dark and sepia mode the raw-blue buttons stayed bright blue
   * against a palette that had moved on. Which colour a button was depended on
   * when it happened to be written.
   *
   * Variants are presentational state, not behaviour switches, so they stay
   * props rather than separate components: `Button` renders one element and the
   * variant only chooses its class string.
   *
   * DESIGN.md's target API also lists an `href` form that renders an `<a>`.
   * There is no call site for it — the frontend contains no `<a>` element at
   * all — so it is deliberately not implemented rather than carried as an
   * untested second code path.
   */
  type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'icon'
  type Size = 'sm' | 'md' | 'lg'

  const VARIANT_CLASSES: Record<Variant, string> = {
    primary: 'bg-accent text-accent-fg hover:bg-accent-hover',
    secondary: 'border border-border text-fg hover:bg-surface-sunken',
    ghost: 'text-fg-muted hover:bg-surface-sunken hover:text-fg',
    danger: 'bg-danger text-white hover:opacity-90',
    icon: 'justify-center text-fg-muted hover:bg-surface-sunken hover:text-fg',
  }

  // `min-h-11` is the 44px coarse-pointer hit area DESIGN.md requires, so every
  // size keeps it and only the inline padding and type scale change.
  const SIZE_CLASSES: Record<Size, string> = {
    sm: 'min-h-11 px-3 text-sm',
    md: 'min-h-11 px-4 text-sm',
    lg: 'min-h-11 px-6 text-base',
  }

  interface Props extends Omit<HTMLButtonAttributes, 'class' | 'children'> {
    variant?: Variant
    size?: Size
    /** Shows a busy state and suppresses clicks while a request is in flight. */
    loading?: boolean
    class?: string
    children: Snippet
  }

  let {
    variant = 'primary',
    size = 'md',
    loading = false,
    disabled = false,
    type = 'button',
    class: className = '',
    children,
    ...rest
  }: Props = $props()

  const isDisabled = $derived(disabled || loading)
  const classes = $derived(
    `inline-flex items-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`
  )
</script>

<button {type} class={classes} disabled={isDisabled} aria-busy={loading} {...rest}>
  {@render children()}
</button>
