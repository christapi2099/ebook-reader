<script lang="ts">
  import { untrack } from 'svelte'
  import { toDetailMessage } from '$lib/utils/errors'
  import Dialog from '$lib/ui/Dialog.svelte'
  import Button from '$lib/ui/Button.svelte'
  import type { Book } from '$lib/api'

  /**
   * Edit the two metadata fields the user owns: the title and the author.
   *
   * Almost every book arrives with a filename as its title and no author at all,
   * and nothing in the imports can tell whether either is right — so this is the
   * only way the card can stop saying "Author not detected". It follows
   * `FolderNameDialog`: the values are validated by the backend and its
   * human-readable `detail` is what gets shown, rather than a second, client-side
   * set of rules that could disagree with it.
   */
  let { book, onClose, onSave }: {
    book: Book
    onClose: () => void
    /** Blank author clears it; an empty title is rejected by the server. */
    onSave: (changes: { title: string; author: string | null }) => Promise<void>
  } = $props()

  // Both fields are seeded once, when the dialog opens; the caller remounts this
  // component (keyed by book id) rather than swapping the props underneath it.
  const initialTitle = untrack(() => book.title)
  const initialAuthor = untrack(() => book.author ?? '')
  let title = $state(initialTitle)
  let author = $state(initialAuthor)
  let saving = $state(false)
  let error = $state<string | null>(null)

  // `null` rather than `''`: clearing the author is the difference between an
  // unknown byline and one that reads as a blank name.
  const authorValue = $derived(author.trim() ? author : null)
  const unchanged = $derived(title === initialTitle && author === initialAuthor)

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault()
    if (saving) return
    if (unchanged) {
      onClose()
      return
    }
    saving = true
    error = null
    try {
      await onSave({ title, author: authorValue })
    } catch (e) {
      error = toDetailMessage(e)
    } finally {
      saving = false
    }
  }
</script>

<Dialog
  titleId="book-metadata-title"
  {onClose}
  closeOnBackdrop
  initialFocus="#book-title-input"
>
  <form class="mx-4 w-full max-w-sm rounded-xl bg-surface p-6 shadow-3" onsubmit={handleSubmit}>
    <h2 id="book-metadata-title" class="text-lg font-bold text-fg">Edit book details</h2>

    <label for="book-title-input" class="mt-4 block text-sm font-medium text-fg">Title</label>
    <input
      id="book-title-input"
      bind:value={title}
      disabled={saving}
      autocomplete="off"
      class="mt-1 min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-sm text-fg placeholder:text-fg-subtle disabled:opacity-60"
    />

    <label for="book-author-input" class="mt-4 block text-sm font-medium text-fg">Author</label>
    <input
      id="book-author-input"
      bind:value={author}
      disabled={saving}
      autocomplete="off"
      class="mt-1 min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-sm text-fg placeholder:text-fg-subtle disabled:opacity-60"
    />
    <p class="mt-1 text-xs text-fg-subtle">
      Leave this blank if the author is unknown — the card will say so.
    </p>

    {#if error}
      <p class="mt-2 text-sm text-danger" role="alert">{error}</p>
    {/if}

    <div class="mt-6 flex justify-end gap-2">
      <Button variant="secondary" onclick={onClose}>Cancel</Button>
      <Button type="submit" variant="primary" disabled={saving}>Save</Button>
    </div>
  </form>
</Dialog>
