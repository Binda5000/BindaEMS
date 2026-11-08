<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { pvRows } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	const rows = $derived(pvRows(stats));
</script>

{#if rows.length > 0}
	<Card title="PV je Anlage">
		<table aria-label="PV je Anlage">
			<thead>
				<tr>
					<th scope="col">Anlage</th>
					<th scope="col" class="num">Energie</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.id)}
					<tr>
						<td class:child={row.child}>{row.name}</td>
						<td class="num">
							{row.value}
							{#if row.perKwp !== '–'}
								<span class="per-kwp">{row.perKwp}</span>
							{/if}
							{#if row.note}
								<span class="note">{row.note}</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</Card>
{/if}

<style>
	th {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	/* Strings stehen eingerückt unter ihrer Anlage */
	.child {
		padding-left: 1.5rem;
	}

	.per-kwp,
	.note {
		display: block;
		font-size: 0.8125rem;
		color: var(--muted);
		white-space: normal;
	}
</style>
