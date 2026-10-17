<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { HistoryResponse, PlanReview } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import type { Palette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { hasComparison, reviewOption, reviewTotals } from './review';

	interface Props {
		review: PlanReview | undefined;
		/** der Verlauf von `soc.battery` für den Tag; ohne ihn bleibt nur der geplante SOC */
		soc: HistoryResponse | undefined;
		error: ApiError | null;
		palette: Palette;
	}

	let { review, soc, error, palette }: Props = $props();

	const TITLE = 'Plan gegen Ist (heute)';

	const compared = $derived(review !== undefined && hasComparison(review));
	const option = $derived(review && compared ? reviewOption(review, soc, palette) : null);
	const rows = $derived(review && compared ? reviewTotals(review) : []);
</script>

<Card title={TITLE}>
	{#if error}
		<Notice level="error">Plan gegen Ist ist nicht verfügbar.</Notice>
	{/if}
	{#if review}
		{#if option}
			<EChart {option} label={TITLE} height="14rem" />
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col"><span class="sr-only">Größe</span></th>
							<th scope="col" class="num">Plan</th>
							<th scope="col" class="num">Ist</th>
							<th scope="col" class="num">Differenz</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.label)}
							<tr>
								<th scope="row">{row.label}</th>
								<td class="num">{row.plan}</td>
								<td class="num">{row.actual}</td>
								<td class="num">{row.diff}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="muted">Für heute gibt es noch keine Viertelstunde mit Plan und Ist.</p>
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0;
	}

	.scroll {
		margin-top: 0.75rem;
	}

	tbody th {
		font-weight: 600;
	}
</style>
