<script lang="ts">
	import { formatEnergy, formatPower } from '$lib/format';
	import type { ConsumerRow } from './tree';

	interface Props {
		rows: ConsumerRow[];
		admin: boolean;
		onEdit: (id: number) => void;
		onDelete: (id: number) => void;
	}

	let { rows, admin, onEdit, onDelete }: Props = $props();
</script>

<table class="tree">
	<caption class="sr-only"
		>Verbraucher mit Leistung und Energie seit Mitternacht; „Sonstiges“ ist der nicht gemessene Rest</caption
	>
	<thead>
		<tr>
			<th scope="col">Verbraucher</th>
			<th scope="col" class="num">Leistung</th>
			<th scope="col" class="num">Heute</th>
			{#if admin}
				<th scope="col"><span class="sr-only">Aktionen</span></th>
			{/if}
		</tr>
	</thead>
	<tbody>
		{#each rows as row (row.key)}
			<tr class={row.kind}>
				<td>
					<span class="name" style:padding-left="{row.depth * 1.25}rem">
						{#if row.kind !== 'other'}
							<span class="swatch" aria-hidden="true" style:background={row.color ?? 'transparent'}
							></span>
						{/if}
						<span>{row.name}</span>
					</span>
					{#if row.kind === 'other' && row.mismatch}
						<span class="mismatch">Unterverbraucher messen mehr als der Elternverbraucher</span>
					{/if}
					{#if row.powerW === null && row.note}
						<!-- bündig mit dem Namen: Farbfeld 0,75 rem + Abstand 0,5 rem -->
						<span class="note" style:padding-left="{(row.depth + 1) * 1.25}rem">{row.note}</span>
					{:else if row.energyKwh === null && row.energyNote}
						<span class="note" style:padding-left="{(row.depth + 1) * 1.25}rem"
							>Energie: {row.energyNote}</span
						>
					{/if}
				</td>
				<td class="num">{formatPower(row.powerW)}</td>
				<td class="num">{formatEnergy(row.energyKwh)}</td>
				{#if admin}
					<td class="actions">
						{#if row.kind === 'consumer' && row.id !== null}
							{@const id = row.id}
							<button type="button" aria-label="{row.name} bearbeiten" onclick={() => onEdit(id)}>
								Bearbeiten
							</button>
							<button type="button" aria-label="{row.name} löschen" onclick={() => onDelete(id)}>
								Löschen
							</button>
						{/if}
					</td>
				{/if}
			</tr>
		{/each}
	</tbody>
</table>

<style>
	.tree th {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.name {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
	}

	.swatch {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 3px;
		flex: none;
	}

	.root td {
		font-weight: 600;
	}

	.other .name {
		font-style: italic;
		color: var(--muted);
	}

	.mismatch {
		display: block;
		color: var(--warn);
		font-size: 0.875rem;
		font-style: normal;
	}

	.note {
		display: block;
		color: var(--muted);
		font-size: 0.875rem;
	}

	.actions {
		text-align: right;
		white-space: nowrap;
	}

	.actions button {
		padding: 0.125rem 0.625rem;
		font-size: 0.875rem;
	}

	.actions button + button {
		margin-left: 0.375rem;
	}
</style>
