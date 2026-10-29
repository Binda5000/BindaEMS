<script lang="ts">
	import type { Decision } from '$lib/api/schemas';
	import { formatDateTime } from '$lib/format';
	import { actuatorDisplay, uniqueDisplays } from '$lib/wallboxes';
	import { displayText, resultLabel } from './view';

	interface Props {
		decisions: Decision[];
		/** Aktor-ID → Bezeichnung des core */
		names: Record<string, string>;
		/** Schlüssel der Wallbox → Name aus den Einstellungen */
		wallboxNames?: Record<string, string>;
	}

	let { decisions, names, wallboxNames = {} }: Props = $props();

	// gleich benannte Wallboxen bleiben unterscheidbar
	const displays = $derived(
		uniqueDisplays(
			Object.entries(names).map(([id, label]) => ({ id, label })),
			wallboxNames
		)
	);
</script>

<div class="scroll">
	<table>
		<thead>
			<tr>
				<th scope="col">Zeit</th>
				<th scope="col">Aktor</th>
				<th scope="col">Entscheidung</th>
				<th scope="col">Ergebnis</th>
			</tr>
		</thead>
		<tbody>
			{#each decisions as decision (decision.id)}
				{@const label = names[decision.actuator] ?? decision.actuator}
				{@const display =
					displays[decision.actuator] ?? actuatorDisplay(decision.actuator, label, wallboxNames)}
				<tr>
					<td class="time">{formatDateTime(decision.ts)}</td>
					<td>{display}</td>
					<td class="text">{displayText(decision.text, label, display)}</td>
					<td>{resultLabel(decision.result)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	/* schmal bleibt die Tabelle lesbar und scrollt in sich */
	table {
		min-width: 36rem;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	td {
		vertical-align: top;
	}

	.time {
		white-space: nowrap;
	}

	.text {
		min-width: 14rem;
	}
</style>
