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
		<div class="scroll wide">
			<table class="stack" aria-label="Autos">
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
							<td class="num" data-label="Energie">{row.energy}</td>
							<td class="num" data-label="PV">{row.pv}</td>
							<td class="num" data-label="Akku">{row.battery}</td>
							<td class="num" data-label="Netz">{row.grid}</td>
							<td class="num" data-label="Kosten">{row.cost}</td>
							<td class="num" data-label="ct/kWh">{row.ctPerKwh}</td>
							<td class="num" data-label="Sessions">{row.sessions}</td>
						</tr>
					{/each}
					{#if view.batteryToVehicles !== null}
						<tr class="flow">
							<td>Akku → Autos</td>
							<td class="num" data-label="Energie">{view.batteryToVehicles}</td>
							<td colspan="6"></td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	{/if}
</Card>

<style>
	.flow td {
		color: var(--muted);
	}
</style>
