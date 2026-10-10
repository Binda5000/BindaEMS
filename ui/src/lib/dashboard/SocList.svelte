<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import Notice from '$lib/components/Notice.svelte';
	import { formatSoc } from '$lib/format';
	import type { SocEntry } from './socs';

	interface Props {
		entries: SocEntry[];
		/** Live-Verbindung veraltet: alle Werte gedämpft */
		stale?: boolean;
		/** Fahrzeuge (Grenzen der Anlage) nicht geladen */
		error?: ApiError | null;
	}

	let { entries, stale = false, error = null }: Props = $props();
</script>

<ul class="socs" class:stale>
	{#each entries as entry (entry.id)}
		<li class={entry.quality}>
			<span class="name">{entry.label}</span>
			<span class="value">
				{formatSoc(entry.pct)}
				{#if entry.quality === 'stale'}
					<span class="tag">veraltet</span>
				{/if}
			</span>
			<span class="bar" aria-hidden="true">
				<span style:width="{Math.min(100, Math.max(0, entry.pct ?? 0))}%"></span>
			</span>
		</li>
	{/each}
</ul>
{#if error}
	<Notice level="error">Fahrzeuge nicht geladen: {error.detail}</Notice>
{/if}

<style>
	.socs {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.75rem;
	}

	li {
		display: grid;
		grid-template-columns: 1fr auto;
		gap: 0.25rem 0.75rem;
	}

	.value {
		font-weight: 600;
		text-align: right;
	}

	.bar {
		grid-column: 1 / -1;
		height: 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		overflow: hidden;
	}

	.bar span {
		display: block;
		height: 100%;
		background: var(--battery);
	}

	.stale li,
	li.stale {
		opacity: 0.6;
	}

	.tag {
		margin-left: 0.25rem;
		font-size: 0.8125rem;
		font-weight: 400;
		color: var(--warn);
	}

	.missing .value {
		color: var(--muted);
	}
</style>
