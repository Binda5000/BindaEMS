<script lang="ts">
	import type { CatalogEntry } from '$lib/api/schemas';
	import { MAX_SERIES } from './ranges';

	interface Props {
		catalog: CatalogEntry[];
		selected: string[];
		onchange: (selected: string[]) => void;
	}

	let { catalog, selected, onchange }: Props = $props();

	const full = $derived(selected.length >= MAX_SERIES);

	function toggle(id: string, checked: boolean) {
		const next = checked ? [...selected, id] : selected.filter((item) => item !== id);
		// Reihenfolge wie im Katalog
		onchange(catalog.map((entry) => entry.id).filter((entry) => next.includes(entry)));
	}
</script>

<fieldset class="picker">
	<legend>Reihen</legend>
	<div class="options">
		{#each catalog as entry (entry.id)}
			{@const checked = selected.includes(entry.id)}
			<label class:disabled={full && !checked}>
				<input
					type="checkbox"
					{checked}
					disabled={full && !checked}
					onchange={(event) => toggle(entry.id, event.currentTarget.checked)}
				/>
				{entry.label} <span class="muted">({entry.unit})</span>
			</label>
		{/each}
	</div>
	{#if full}
		<p class="hint muted">Höchstens {MAX_SERIES} Reihen gleichzeitig.</p>
	{/if}
</fieldset>

<style>
	.picker {
		border: none;
		margin: 0;
		padding: 0;
		min-width: 0;
	}

	legend {
		font-weight: 600;
		padding: 0;
		margin-bottom: 0.5rem;
	}

	.options {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1rem;
	}

	label {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		min-height: 2.75rem;
	}

	.disabled {
		opacity: 0.55;
	}

	.hint {
		margin: 0.25rem 0 0;
		font-size: 0.875rem;
	}
</style>
