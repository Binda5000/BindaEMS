<script lang="ts">
	import type { LegendItem } from './legend';

	interface Props {
		items: LegendItem[];
		hidden: ReadonlySet<string>;
		onToggle: (name: string) => void;
		/** Name des Diagramms, damit mehrere Chip-Gruppen auf einer Seite unterscheidbar sind */
		label?: string;
	}

	let { items, hidden, onToggle, label = '' }: Props = $props();
</script>

<div
	class="legend"
	role="group"
	aria-label={label === ''
		? 'Reihen ein- und ausblenden'
		: `Reihen von ${label} ein- und ausblenden`}
>
	{#each items as item (item.name)}
		<button
			type="button"
			class="chip"
			class:off={hidden.has(item.name)}
			aria-pressed={!hidden.has(item.name)}
			onclick={() => onToggle(item.name)}
		>
			<span class="mark {item.mark}" style:--mark={item.color} aria-hidden="true"></span>
			<span class="name">{item.name}</span>
		</button>
	{/each}
</div>

<style>
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.375rem;
		margin-top: 0.25rem;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		min-height: 2.75rem;
		max-width: 100%;
		padding: 0 0.75rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface);
		color: var(--text);
		font: inherit;
		font-size: 0.875rem;
		cursor: pointer;
	}
	.chip.off {
		color: var(--muted);
	}
	.chip.off .mark {
		opacity: 0.4;
	}
	.chip.off .name {
		text-decoration: line-through;
	}
	.name {
		overflow-wrap: anywhere;
		text-align: left;
	}
	.mark {
		flex: none;
		display: inline-block;
	}
	.mark.line {
		width: 1.25rem;
		height: 3px;
		border-radius: 2px;
		background: var(--mark);
	}
	.mark.dashed {
		width: 1.25rem;
		height: 3px;
		background: repeating-linear-gradient(90deg, var(--mark) 0 5px, transparent 5px 8px);
	}
	.mark.area {
		width: 1.25rem;
		height: 0.75rem;
		border: 1px solid var(--mark);
		border-radius: 2px;
		background: color-mix(in srgb, var(--mark) 25%, transparent);
	}
	.mark.rect {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 2px;
		background: var(--mark);
	}
</style>
