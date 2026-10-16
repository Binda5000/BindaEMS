<script lang="ts">
	import type { Decision } from '$lib/api/schemas';
	import { formatDateTime } from '$lib/format';
	import { resultLabel } from './view';

	interface Props {
		decisions: Decision[];
		/** Aktor-ID → Anzeigename */
		names: Record<string, string>;
	}

	let { decisions, names }: Props = $props();
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
				<tr>
					<td class="time">{formatDateTime(decision.ts)}</td>
					<td>{names[decision.actuator] ?? decision.actuator}</td>
					<td class="text">{decision.text}</td>
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
