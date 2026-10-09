<script lang="ts">
	import { ageText, type SignalRow } from './view';

	interface Props {
		rows: SignalRow[];
	}

	let { rows }: Props = $props();

	const QUALITY = { ok: '', stale: 'veraltet', invalid: 'ungültig' } as const;
</script>

<div class="scroll">
	<table class="signals">
		<thead>
			<tr>
				<th scope="col">Signal</th>
				<th scope="col" class="num">Wert</th>
				<th scope="col">Qualität</th>
				<th scope="col" class="num">Alter</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.name)}
				<tr class={row.quality}>
					<td class="name">{row.name}</td>
					<td class="num">{row.value}</td>
					<td>
						{#if row.quality !== 'ok'}
							<span class="quality">{QUALITY[row.quality]}</span>
						{/if}
					</td>
					<td class="num muted">{ageText(row.ageS)}</td>
				</tr>
			{:else}
				<tr><td colspan="4" class="muted">Keine Signale</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.scroll {
		overflow: auto;
		max-height: 32rem;
	}

	.signals th {
		position: sticky;
		top: 0;
		background: var(--surface);
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.name {
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		font-size: 0.875rem;
		word-break: break-all;
	}

	.quality {
		font-size: 0.8125rem;
		font-weight: 600;
	}

	.stale .quality {
		color: var(--warn);
	}

	.invalid .quality {
		color: var(--error);
	}

	.stale td,
	.invalid td {
		opacity: 0.85;
	}
</style>
