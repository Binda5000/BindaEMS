<script lang="ts">
	// Vorheizen und Vorkühlen im Zeitplan (Plan 5c): die Zeile zu Vorschau, aktiver Verschiebung
	// oder Grund und darunter die Spalte „Klima“ je Zeitraum gleicher Verschiebung
	import type { Plan, PlanThermal } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { formatTime } from '$lib/format';
	import { planThermalLine, thermalRuns } from '$lib/learning/thermal';

	interface Props {
		thermal: PlanThermal;
		slots: Plan['slots'];
	}

	let { thermal, slots }: Props = $props();

	const runs = $derived(thermalRuns(slots, thermal.mode));
</script>

<Card title="Vorheizen und Vorkühlen">
	<p class="line">{planThermalLine(thermal)}</p>
	{#if runs.length > 0}
		<table>
			<thead>
				<tr>
					<th scope="col">Zeit</th>
					<th scope="col" class="num">Klima</th>
				</tr>
			</thead>
			<tbody>
				{#each runs as run (run.start)}
					<tr>
						<th scope="row" class="time">{formatTime(run.start)}–{formatTime(run.end)}</th>
						<td class="num">{run.shift}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</Card>

<style>
	.line {
		margin: 0 0 0.75rem;
		overflow-wrap: anywhere;
	}

	.time {
		font-weight: 400;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
