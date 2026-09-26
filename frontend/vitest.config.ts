import { sveltekit } from '@sveltejs/kit/vite'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [sveltekit()],
  // Component tests mount real components, which needs Svelte's client build.
  // Vitest resolves the server build by default, where mount() is unavailable.
  // This file only configures Vitest — the dev server and build use vite.config.ts.
  resolve: {
    conditions: ['browser'],
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/tests/setup.ts'],
  },
})
