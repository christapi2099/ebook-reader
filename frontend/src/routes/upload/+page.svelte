<script lang="ts">
  import { goto } from '$app/navigation'
  import UploadDialog from '$lib/components/UploadDialog.svelte'

  let uploadOpen = $state(true)

  function handleClose() {
    history.back()
  }

  function handleUploaded(bookId: string) {
    goto(`/reader/${bookId}`)
  }
</script>

<div class="flex h-full items-center justify-center text-slate-400 text-sm">
  Select a file to upload
</div>

<UploadDialog
  open={uploadOpen}
  onClose={handleClose}
  onUploaded={handleUploaded}
/>
