<script lang="ts">
	import type { Stats } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { vehicleRows } from './view';

	interface Props {
		stats: Stats;
	}

	let { stats }: Props = $props();

	const view = $derived(vehicleRows(stats));
</script>

<Card title="Autos">
	{#if view.rows.length === 0}
		<p class="muted">Im Zeitraum wurde kein Auto geladen.</p>
	{:else}
		<div class="scroll">
			<table aria-label="Autos">
				<thead>
					<tr>
						<th scope="col">Auto</th>
						<th scope="col" class="num">Energie</th>
						<th scope="col" class="num">PV</th>
						<th scope="col" class="num">Akku</th>
						<th scope="col" class="num">Netz</th>
						<th scope="col" class="num">Kosten</th>
						<th scope="col" class="num">ct/kWh</th>
						<th scope="col" class="num">Sessions</th>
					</tr>
				</thead>
				<tbody>
					{#each view.rows as row (row.key)}
						<tr>
							<td>{row.name}</td>
							<td class="num">{row.energy}</td>
							<td class="num">{row.pv}</td>
							<td class="num">{row.battery}</td>
							<td class="num">{row.grid}</td>
							<td class="num">{row.cost}</td>
							<td class="num">{row.ctPerKwh}</td>
							<td class="num">{row.sessions}</td>
						</tr>
					{/each}
					{#if view.batteryToVehicles !== null}
						<tr class="flow">
							<td>Akku → Autos</td>
							<td class="num">{view.batteryToVehicles}</td>
							<td colspan="6"></td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	{/if}
</Card>

<style>
	table {
		min-width: max-content;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
		white-space: nowrap;
	}

	td {
		white-space: nowrap;
	}

	.flow td {
		color: var(--muted);
	}
</style>
