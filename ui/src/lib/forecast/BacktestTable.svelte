<script lang="ts">
	import type { LearningStatus } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import { DASH } from '$lib/format';
	import { backtestRows } from './view';

	interface Props {
		/** `undefined`: der Stand ist noch nicht da */
		status: LearningStatus | undefined;
	}

	let { status }: Props = $props();

	const view = $derived(status?.backtest ? backtestRows(status.backtest) : null);
</script>

<Card title="Backtest">
	{#if view && view.weeks.length > 0}
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col">Woche ab</th>
						<th scope="col">Vorlauf</th>
						<th scope="col" class="num">Slots</th>
						<th scope="col" class="num">Modell</th>
						<th scope="col" class="num">Profil</th>
						<th scope="col" class="num">besser</th>
					</tr>
				</thead>
				<tbody>
					{#each view.weeks as week (week.key)}
						<tr>
							<td>{week.origin}</td>
							<td>{week.horizon}</td>
							<td class="num">{week.slots}</td>
							<td class="num">{week.model}</td>
							<td class="num">{week.profile}</td>
							<td class="num">{week.better}</td>
						</tr>
					{/each}
				</tbody>
				<tfoot>
					<tr>
						<th scope="row">Gesamt</th>
						<td>{DASH}</td>
						<td class="num">{view.total.slots}</td>
						<td class="num">{view.total.model}</td>
						<td class="num">{view.total.profile}</td>
						<td class="num">{view.total.better}</td>
					</tr>
				</tfoot>
			</table>
		</div>
	{:else if status}
		<p class="muted">Noch kein Backtest (braucht mindestens 28 Tage Daten).</p>
	{:else}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}

	tfoot th,
	tfoot td {
		font-weight: 600;
	}
</style>
