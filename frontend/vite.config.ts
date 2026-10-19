import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

// Mirrors the routes nginx.conf proxies to the backend, so `pnpm run preview`
// serves the production build against a local backend the same way Docker does.
const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000';
// `/library` and `/mp3` are also pages. A browser navigation asks for text/html,
// an API call does not, so navigations fall through to the SPA.
const isNavigation = (req: { headers: { accept?: string } }) =>
	req.headers.accept?.includes('text/html') ? '/' : undefined;
const backendPaths = [
	'documents', 'library', 'user', 'voices', 'mp3', 'bookmarks', 'folders', 'api', 'uploads', 'health'
];

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	preview: {
		proxy: {
			...Object.fromEntries(backendPaths.map((p) => [`^/${p}(/|$)`, { target: backend, bypass: isNavigation }])),
			'^/ws/': { target: backend, ws: true }
		}
	}
});
