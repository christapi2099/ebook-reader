<script lang="ts">
  import '../app.css'
  import favicon from '$lib/assets/favicon.svg'
  import { page } from '$app/stores'
  import { goto } from '$app/navigation'
  import Sidebar from '$lib/components/Sidebar.svelte'

  let { children } = $props()
  let sidebarOpen = $state(false)
</script>

<svelte:head>
  <link rel="icon" href={favicon} />
</svelte:head>

<!-- Mobile hamburger -->
<button
  class="md:hidden fixed top-3 left-3 z-50 p-3 rounded-lg bg-white shadow-md border border-slate-200"
  onclick={() => (sidebarOpen = !sidebarOpen)}
  aria-label="Toggle menu"
>
  <svg class="w-5 h-5 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
  </svg>
</button>

<!-- Mobile overlay -->
{#if sidebarOpen}
  <div
    class="md:hidden fixed inset-0 bg-black/40 z-30"
    onclick={() => (sidebarOpen = false)}
    role="presentation"
  ></div>
{/if}

<div class="flex h-screen overflow-hidden">
  <!-- Sidebar: always visible md+, drawer on mobile -->
  <div class={[
    'fixed top-0 left-0 h-screen z-40 transition-transform duration-200',
    'md:translate-x-0',
    sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
  ].join(' ')}>
    <Sidebar
      activeRoute={$page.url.pathname}
      onNavigate={(route) => { goto(route); sidebarOpen = false }}
    />
  </div>

  <main class="md:ml-[180px] flex-1 overflow-auto bg-white pt-12 md:pt-0">
    {@render children()}
  </main>
</div>
