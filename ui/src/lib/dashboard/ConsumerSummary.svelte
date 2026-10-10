<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { TreeNode } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { consumerRows } from '$lib/consumers/tree';
	import { formatEnergy, formatPower, formatRatio } from '$lib/format';

	interface Props {
		tree: TreeNode | undefined;
		/** Fehler der letzten Ladung; vorhandene Werte gelten dann als veraltet */
		error: ApiError | null;
	}

	let { tree, error }: Props = $props();

	const rows = $derived(tree ? consumerRows(tree).filter((row) => row.depth <= 1) : []);

	function share(powerW: number | null): string {
		const house = tree?.power_w ?? null;
		return house !== null && house > 0 && powerW !== null ? formatRatio(powerW / house) : '';
	}
</script>

{#if error}
	<Notice level="error">{error.detail}</Notice>
{/if}
{#if !tree}
	{#if !error}
		<p class="muted">Lädt …</p>
	{/if}
{:else}
	{#if error}
		<p class="stale">veraltet</p>
	{/if}
	<table class="summary" data-stale={error !== null}>
		<caption class="sr-only"
			>Verbraucher mit Leistung (Anteil an der Hauslast darunter) und Energie seit Mitternacht</caption
		>
		<thead>
			<tr>
				<th scope="col"><span class="sr-only">Verbraucher</span></th>
				<th scope="col" class="num">Jetzt</th>
				<th scope="col" class="num">Heute</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.key)}
				<tr class={row.kind}>
					<td>
						<span class="name"
							><span class="swatch" aria-hidden="true" style:background={row.color ?? 'transparent'}
							></span>{row.name}</span
						>
						{#if row.kind === 'other' && row.mismatch}
							<span class="mismatch">(Unterverbraucher messen mehr)</span>
						{/if}
					</td>
					<td class="num">
						{formatPower(row.powerW)}
						{#if row.kind !== 'root' && share(row.powerW)}
							<span class="share muted">{share(row.powerW)}</span>
						{/if}
					</td>
					<td class="num">{formatEnergy(row.energyKwh)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
<p class="more"><a href="/verbraucher">Alle Verbraucher</a></p>

<style>
	.summary td,
	.summary th {
		border-bottom: none;
		padding: 0.25rem 0.375rem;
	}

	.summary {
		width: 100%;
	}

	.summary .num {
		white-space: nowrap;
	}

	.share {
		display: block;
		font-size: 0.8125rem;
	}

	.summary th {
		font-size: 0.8125rem;
		font-weight: 400;
		color: var(--muted);
	}

	.root td {
		font-weight: 600;
		border-bottom: 1px solid var(--border);
	}

	.consumer td:first-child,
	.other td:first-child {
		padding-left: 1.25rem;
	}

	.other td:first-child {
		font-style: italic;
	}

	.name {
		display: inline-flex;
		align-items: baseline;
		/* in Tabellen zählt nur „anywhere“: lange Namen brechen, statt die Seite zu verbreitern */
		overflow-wrap: anywhere;
		hyphens: auto;
	}

	/* drei Spalten bei 360 px auch mit breiter Schrift (DejaVu Sans in der CI) */
	@media (max-width: 400px) {
		.summary td,
		.summary th {
			padding: 0.25rem;
		}

		.consumer td:first-child,
		.other td:first-child {
			padding-left: 0.75rem;
		}

		.summary .num {
			font-size: 0.875rem;
		}
	}

	.swatch {
		flex: none;
		display: inline-block;
		width: 0.625rem;
		height: 0.625rem;
		margin-right: 0.375rem;
		border-radius: 2px;
	}

	.mismatch {
		color: var(--warn);
		font-size: 0.875rem;
	}

	.summary[data-stale='true'] {
		opacity: 0.55;
	}

	.stale {
		margin: 0.5rem 0 0;
		color: var(--warn);
		font-size: 0.875rem;
	}

	.more {
		margin: 0.75rem 0 0;
	}
</style>
