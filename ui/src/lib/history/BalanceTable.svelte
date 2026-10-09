<script lang="ts">
	import { BALANCE_COLUMNS, type BalanceRow } from './balance';

	interface Props {
		rows: BalanceRow[];
	}

	let { rows }: Props = $props();
</script>

<p class="hint muted">Zählerstände für den Abgleich mit VRM: je Tag unter „Zähler“ aufklappen.</p>
<div class="scroll">
	<table>
		<thead>
			<tr>
				<th scope="col">Tag</th>
				{#each BALANCE_COLUMNS as column (column.key)}
					<th scope="col" class="num">{column.label}</th>
				{/each}
				<th scope="col">Zähler</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.date)}
				<tr>
					<td class="day">{row.day}</td>
					{#each BALANCE_COLUMNS as column (column.key)}
						<td class="num">{row.cells[column.key]}</td>
					{/each}
					<td>
						{#if row.counters.length > 0}
							<details>
								<summary>Zählerstände</summary>
								<dl>
									{#each row.counters as counter (counter.name)}
										<dt>{counter.name}</dt>
										<dd>{counter.value}</dd>
									{/each}
								</dl>
							</details>
						{:else}
							<span class="muted">–</span>
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.hint {
		margin: 0 0 0.5rem;
		font-size: 0.875rem;
	}

	.scroll {
		overflow-x: auto;
		max-width: 100%;
	}

	table {
		min-width: max-content;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
		white-space: nowrap;
	}

	.day {
		white-space: nowrap;
		font-weight: 600;
	}

	dl {
		display: grid;
		grid-template-columns: auto auto;
		gap: 0 0.75rem;
		margin: 0.25rem 0 0;
	}

	dd {
		margin: 0;
		text-align: right;
		white-space: nowrap;
	}
</style>
