<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		level: 'error' | 'warning' | 'info';
		children: Snippet;
	}

	let { level, children }: Props = $props();

	const PREFIX = { error: 'Fehler', warning: 'Warnung', info: 'Hinweis' } as const;
</script>

<div class="notice {level}">
	<span class="sr-only">{PREFIX[level]}:</span>
	{@render children()}
</div>

<style>
	.notice {
		padding: 0.5rem 0.75rem;
		border-left: 4px solid var(--muted);
		border-radius: 4px;
		background: var(--surface-2);
		white-space: pre-line;
	}

	.error {
		border-left-color: var(--error);
	}

	.warning {
		border-left-color: var(--warn);
	}

	.info {
		border-left-color: var(--accent);
	}
</style>
