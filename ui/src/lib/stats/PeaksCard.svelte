<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { peakRows } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	// nur Monat und Jahr kennen Monatsspitzen
	const rows = $derived(stats.period === 'month' || stats.period === 'year' ? peakRows(stats) : []);
</script>

{#if rows.length > 0}
	<Card title="Monatsspitzen">
		<div class="scroll wide">
			<table aria-label="Monatsspitzen">
				<thead>
					<tr>
						<th scope="col">Monat</th>
						<th scope="col" class="num">Spitze</th>
						<th scope="col">Zeitpunkt</th>
						<th scope="col" class="num">Leistungspreis</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.month)}
						<tr>
							<td>{row.month}</td>
							<td class="num">{row.peak}</td>
							<td>{row.at}</td>
							<td class="num">{row.price}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card>
{/if}
