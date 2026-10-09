<script lang="ts">
	import { untrack } from 'svelte';
	import { ApiError } from '$lib/api/errors';
	import Notice from '$lib/components/Notice.svelte';

	interface Props {
		initial: { first: string; last: string };
		reprice: (first: string, last: string) => Promise<{ repriced: number }>;
	}

	let { initial, reprice }: Props = $props();

	const uid = $props.id();
	let first = $state(untrack(() => initial.first));
	let last = $state(untrack(() => initial.last));
	let busy = $state(false);
	let result = $state<{ level: 'info' | 'error'; text: string } | null>(null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		result = null;
		if (!first || !last || first > last) {
			result = { level: 'error', text: 'Der erste Tag muss vor dem letzten liegen.' };
			return;
		}
		busy = true;
		try {
			const { repriced } = await reprice(first, last);
			result = { level: 'info', text: `${repriced} Viertelstunden neu bewertet.` };
		} catch (error) {
			result = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		} finally {
			busy = false;
		}
	}
</script>

<form class="reprice" onsubmit={submit} novalidate>
	<p class="hint">
		Die Abrechnung speichert den Bezugspreis je Viertelstunde. Nach einer Tarifänderung die
		betroffenen Tage neu bewerten.
	</p>
	<div class="dates">
		<label for="{uid}-first">Erster Tag</label>
		<input id="{uid}-first" type="date" bind:value={first} />
		<label for="{uid}-last">Letzter Tag</label>
		<input id="{uid}-last" type="date" bind:value={last} />
	</div>
	<p><button type="submit" disabled={busy}>Neu bewerten</button></p>
	{#if result}
		<Notice level={result.level}>{result.text}</Notice>
	{/if}
</form>

<style>
	.reprice {
		display: grid;
		gap: 0.5rem;
	}

	.hint {
		margin: 0;
		color: var(--muted);
		font-size: 0.9375rem;
	}

	.dates {
		display: grid;
		grid-template-columns: max-content minmax(0, 12rem);
		justify-content: start;
		align-items: center;
		gap: 0.5rem 0.75rem;
	}

	p {
		margin: 0;
	}
</style>
