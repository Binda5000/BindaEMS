<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Quality } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import { PERIODS, compareRows, pvRows, qualityRows, qualitySummary } from './view';

	interface Props {
		data: Quality | undefined;
		error: ApiError | null;
		/** gewählter Zeitraum in Tagen */
		days: number;
		onSelect: (days: number) => void;
	}

	let { data, error, days, onSelect }: Props = $props();

	const rows = $derived(qualityRows(data?.rows ?? []));
	const summary = $derived(qualitySummary(data?.rows ?? []));
	const compare = $derived(compareRows(data?.rows ?? []));
	const pv = $derived(pvRows(data?.rows ?? []));
	const periods = PERIODS.map((period) => ({ id: period, label: `${period} Tage` }));
	// die Werte gehören noch zum vorigen Zeitraum, bis die Antwort da ist
	const stale = $derived(data !== undefined && data.days !== days);
</script>

<Card title="Prognosegüte">
	<div class="period">
		<Segmented
			options={periods}
			value={days}
			label="Zeitraum wählen"
			onSelect={(id) => onSelect(Number(id))}
		/>
	</div>
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		<div class="body" class:stale aria-busy={stale}>
			{#if rows.length > 0}
				{#if summary}
					<p class="summary">{summary}</p>
				{/if}
				{#if compare.length > 0}
					<div class="scroll">
						<table class="compare">
							<thead>
								<tr>
									<th scope="col">Vorlauf</th>
									<th scope="col" class="num">Profil</th>
									<th scope="col" class="num">Modell</th>
									<th scope="col" class="num">im Plan</th>
								</tr>
							</thead>
							<tbody>
								{#each compare as row (row.horizon)}
									<tr>
										<td>{row.horizon}</td>
										<td class="num">{row.profile}</td>
										<td class="num">{row.model}</td>
										<td class="num">{row.used}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<p class="muted note">Mittlere Abweichung je Viertelstunde, kleiner ist besser.</p>
				{/if}
				{#each pv as line (line)}
					<p class="pv">{line}</p>
				{/each}
				<details>
					<summary>Alle Werte</summary>
					<div class="scroll">
						<table class="stack">
							<thead>
								<tr>
									<th scope="col">Art</th>
									<th scope="col">Vorlauf</th>
									<th scope="col" class="num">Viertelstunden</th>
									<th scope="col" class="num">MAE</th>
									<th scope="col" class="num">Bias</th>
									<th scope="col" class="num">rel. MAE</th>
									<th scope="col" class="num">besser als Profil</th>
								</tr>
							</thead>
							<tbody>
								{#each rows as row (row.key)}
									<tr>
										<td>{row.kind}</td>
										<td data-label="Vorlauf">{row.horizon}</td>
										<td class="num" data-label="Viertelstunden">{row.slots}</td>
										<td class="num" data-label="MAE">{row.mae}</td>
										<td class="num" data-label="Bias">{row.bias}</td>
										<td class="num" data-label="rel. MAE">{row.rel}</td>
										<td class="num" data-label="besser als Profil">{row.better}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</details>
			{:else}
				<p class="muted">Noch keine Werte im Zeitraum.</p>
			{/if}
		</div>
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	.period {
		margin-bottom: 0.75rem;
	}

	.stale {
		opacity: 0.5;
		transition: opacity 0.2s;
	}

	p {
		margin: 0;
	}

	.summary {
		margin-bottom: 0.75rem;
		font-weight: 600;
	}

	.note {
		margin: 0.5rem 0 0.75rem;
		font-size: 0.875rem;
	}

	.pv {
		font-size: 0.875rem;
	}

	details {
		margin-top: 0.5rem;
	}

	/* vier Spalten müssen bei 360 px auch mit breiter Systemschrift in die Karte passen */
	.compare th,
	.compare td {
		padding-inline: 0.375rem;
	}

	.compare th {
		white-space: normal;
	}
</style>
