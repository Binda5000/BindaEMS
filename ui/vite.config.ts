import { sveltekit } from '@sveltejs/kit/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

// Entwicklung: API und Live-WebSocket an ems-app oder das Demo-Backend (tests/e2e/ui_server.py)
const backend = process.env.BINDAEMS_BACKEND ?? 'http://127.0.0.1:8099';

export default defineConfig({
	plugins: [sveltekit(), ...(process.env.VITEST ? [svelteTesting()] : [])],
	server: {
		proxy: {
			// Host unverändert lassen: der Live-WebSocket prüft die Herkunft dagegen (sonst 4403)
			'/api': { target: backend, ws: true, changeOrigin: false },
			'/health': { target: backend, changeOrigin: false }
		}
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['src/test-setup.ts']
	}
});
