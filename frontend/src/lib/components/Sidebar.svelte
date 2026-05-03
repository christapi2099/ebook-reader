<script lang="ts">
  let { activeRoute, onNavigate }: { activeRoute: string; onNavigate: (route: string) => void } =
    $props()

  const navItems = [
    {
      name: 'Text Reader',
      route: '/',
      icon: '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>',  // Speaker/headphone icon
    },
    {
      name: 'Library',
      route: '/library',
      icon: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 17V5.5a2.5 2.5 0 0 1 2.5-2.5h9A2.5 2.5 0 0 1 20 5.5v12" />',
    },
    {
      name: 'Voice',
      route: '/voice',
      icon: '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>',
    },
    {
      name: 'MP3',
      route: '/mp3',
      icon: '<path d="M9 18V5l12-2v13M9 9l12-2M9 13l12-2"/>',
    },
  ]
</script>

<aside class="fixed left-0 top-0 h-screen w-[180px] bg-slate-50 border-r border-slate-200 flex flex-col z-40">
  <div class="p-4 border-b border-slate-200">
    <h1 class="text-lg font-bold text-slate-800">EbookReader</h1>
  </div>

  <nav class="flex-1 py-3 space-y-0.5 px-2">
    {#each navItems as item}
      <button
        class={[
          'w-full flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
          activeRoute === item.route
            ? 'bg-blue-50 text-blue-600'
            : 'text-slate-700 hover:bg-slate-100',
        ].join(' ')}
        onclick={() => onNavigate(item.route)}
      >
        <svg
          class="w-5 h-5 flex-shrink-0"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          {@html item.icon}
        </svg>
        {item.name}
      </button>
    {/each}
  </nav>

  <div class="p-2 border-t border-slate-200">
    <button
      class="w-full flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 transition-colors"
      onclick={() => onNavigate('/account')}
    >
      <svg
        class="w-5 h-5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
      </svg>
      Account
    </button>
  </div>
</aside>
