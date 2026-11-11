<script lang="ts">
	import type { PvPeriod, PvSource } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import { theme } from '$lib/theme.svelte';
	import {
		formatPeriodValue,
		hasStringEntries,
		periodEntries,
		periodOption,
		periodTable,
		type PvUnit,
		type PvView
	} from './charts';

	interface Props {
		period: PvPeriod;
		sources: PvSource[];
		/** heute (YYYY-MM-DD): spätere Buckets sind Zukunft, keine fehlenden Daten */
		today: string;
	}

	let { period, sources, today }: Props = $props();

	const VIEWS = [
		{ id: 'systems', label: 'Anlagen' },
		{ id: 'strings', label: 'Strings' }
	] as const;
	const UNITS = [
		{ id: 'kwh', label: 'kWh' },
		{ id: 'kwp', label: 'kWh/kWp' }
	] as const;

	let chosenView = $state<PvView>('systems');
	// je Ansicht gewählt; Strings vergleicht man je kWp (verschieden groß)
	let units = $state<Record<PvView, PvUnit>>({ systems: 'kwh', strings: 'kwp' });

	const strings = $derived(hasStringEntries(sources));
	const view = $derived<PvView>(strings ? chosenView : 'systems');
	// ohne einen Eintrag mit kWp gibt es nichts je kWp zu zeigen
	const canKwp = $derived(periodEntries(sources, view, 'kwp').shown.length > 0);
	const unit = $derived<PvUnit>(canKwp ? units[view] : 'kwh');
	const entries = $derived(periodEntries(sources, view, unit));
	const title = $derived(period.period === 'year' ? 'Je Monat' : 'Je Tag');
	const caption = $derived(period.period === 'year' ? 'Ertrag je Monat' : 'Ertrag je Tag');

	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(periodOption(period, entries.shown, unit, today, palette));
	const table = $derived(periodTable(period, entries.shown, unit));
</script>

<Card {title}>
	<div class="toggles">
		{#if strings}
			<Segmented
				options={VIEWS}
				value={view}
				label="Anlagen oder Strings zeigen"
				onSelect={(id) => (chosenView = id as PvView)}
			/>
		{/if}
		{#if canKwp}
			<Segmented
				options={UNITS}
				value={unit}
				label="Einheit wählen"
				onSelect={(id) => (units = { ...units, [view]: id as PvUnit })}
			/>
		{/if}
	</div>

	{#if entries.shown.length > 0}
		<EChart {option} label="{caption} in {unit === 'kwh' ? 'kWh' : 'kWh/kWp'}" height="16rem" />
		<!-- die Werte für Screenreader; der Rahmen schneidet die Tabelle ab (sie selbst wüchse) -->
		<div class="sr-only">
			<table aria-label={caption}>
				<thead>
					<tr>
						<th scope="col">{period.period === 'year' ? 'Monat' : 'Tag'}</th>
						{#each table.rows as row (row.source.id)}
							<th scope="col">{row.source.name}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each table.titles as title, index (index)}
						<tr>
							<th scope="row">{title}</th>
							{#each table.rows as row (row.source.id)}
								<td>{formatPeriodValue(row.values[index], unit)}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="muted">Keine Einträge zum Zeigen.</p>
	{/if}
	{#if entries.withoutKwp.length > 0}
		<p class="hint">ohne kWp: {entries.withoutKwp.map((source) => source.name).join(', ')}</p>
	{/if}
</Card>

<style>
	.toggles {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}

	.toggles > :global(*) {
		flex: 1 1 9rem;
	}

	.hint {
		margin: 0.5rem 0 0;
		color: var(--muted);
		font-size: 0.875rem;
	}
</style>
