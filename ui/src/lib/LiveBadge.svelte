<script lang="ts">
	import type { LiveStatus } from './live.svelte';

	interface Props {
		status: LiveStatus;
		stale: boolean;
		coreConnected: boolean;
	}

	let { status, stale, coreConnected }: Props = $props();

	const view = $derived.by((): { text: string; tone: 'ok' | 'warn' | 'error' } => {
		switch (status) {
			case 'open':
				if (!coreConnected) return { text: 'core getrennt', tone: 'error' };
				return stale ? { text: 'veraltet', tone: 'warn' } : { text: 'live', tone: 'ok' };
			case 'connecting':
			case 'reconnecting':
				return { text: 'verbinde …', tone: 'warn' };
			case 'unauthorized':
				return { text: 'abgemeldet', tone: 'error' };
			case 'forbidden':
				return { text: 'abgelehnt', tone: 'error' };
			case 'stopped':
				return { text: 'getrennt', tone: 'warn' };
		}
	});
</script>

<span class="badge {view.tone}" role="status" title="Live-Verbindung: {view.text}">
	<span class="dot" aria-hidden="true"></span>{view.text}
</span>

<style>
	.badge {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		padding: 0.125rem 0.625rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		font-size: 0.8125rem;
		white-space: nowrap;
		color: var(--text);
		background: var(--surface);
	}

	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: currentColor;
	}

	.ok .dot {
		background: var(--ok);
	}

	.warn .dot {
		background: var(--warn);
	}

	.error .dot {
		background: var(--error);
	}

	.ok .dot {
		animation: pulse 2s ease-in-out infinite;
	}

	@keyframes pulse {
		50% {
			opacity: 0.4;
		}
	}
</style>
