<script lang="ts">
	import type { HeatingStats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { heatingHints, heatingRows } from './view';

	interface Props {
		heating: HeatingStats;
		/** erster Tag des Zeitraums der Statistik */
		first: string;
	}

	let { heating, first }: Props = $props();

	const rows = $derived(heatingRows(heating));
	const hints = $derived(heatingHints(heating, first));
</script>

<Card title="Heizung">
	<table aria-label="Heizung">
		<tbody>
			{#each rows as row (row.id)}
				<tr>
					<th scope="row">{row.label}</th>
					<td class="num">
						{row.value}
						{#if row.note}<span class="note">{row.note}</span>{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
	{#each hints as hint (hint)}
		<p class="hint muted">{hint}</p>
	{/each}
</Card>

<style>
	table {
		width: 100%;
	}

	th,
	td {
		padding: 0.375rem 0;
		vertical-align: top;
	}

	th {
		font-weight: normal;
		text-align: left;
	}

	.num {
		text-align: right;
	}

	.note {
		display: block;
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.hint {
		margin: 0.5rem 0 0;
		font-size: 0.875rem;
	}
</style>
