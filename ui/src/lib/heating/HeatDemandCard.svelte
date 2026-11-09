<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import type { HeatDemand } from '$lib/api/schemas';
	import EChart from '$lib/charts/EChart.svelte';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { theme } from '$lib/theme.svelte';
	import {
		acMonthRows,
		acNote,
		fitLines,
		heatDemandOption,
		heatingCsv,
		heatingFilename,
		seasonTotalsText
	} from './view';

	interface Props {
		/** gewählter Tag der Statistik (`JJJJ-MM-TT`); seine Heizsaison (1.7. bis 30.6.) wird gezeigt */
		day: string;
	}

	const REVOKE_MS = 40_000;
	const RETRY_MS = 30_000;

	let { day }: Props = $props();

	// Die Saison ändert sich nur mit dem Jahr oder dem Juli: innerhalb davon nicht neu laden
	const seasonKey = $derived.by(() => {
		const [year, month] = day.split('-').map(Number);
		return month >= 7 ? year : year - 1;
	});

	let resource = $state.raw<Resource<HeatDemand> | null>(null);
	$effect(() => {
		void seasonKey;
		const date = untrack(() => day);
		const next = new Resource<HeatDemand>((o) => api.heatDemand(date, o), {
			retryMs: RETRY_MS,
			// ohne Brennerstatus gibt es die Karte nicht
			giveUp: (error) => error.status === 404
		});
		resource = next;
		return next.start();
	});

	// beim Wechsel der Saison bleibt die vorige stehen, bis die neue da ist
	let shown = $state.raw<HeatDemand | undefined>(undefined);
	$effect(() => {
		if (resource?.data !== undefined) shown = resource.data;
	});
	const demand = $derived(resource?.data ?? shown);
	const hidden = $derived(resource?.error?.status === 404);

	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});
	const option = $derived(demand ? heatDemandOption(demand, palette) : null);
	const lines = $derived(demand ? fitLines(demand) : []);
	const months = $derived(demand ? acMonthRows(demand) : []);

	function download() {
		if (!demand) return;
		const url = URL.createObjectURL(
			new Blob([heatingCsv(demand)], { type: 'text/csv;charset=utf-8' })
		);
		const link = document.createElement('a');
		link.href = url;
		link.download = heatingFilename(demand);
		link.click();
		// Safari/iOS und manche Firefox brechen den Download ab, wenn die Adresse sofort fällt
		setTimeout(() => URL.revokeObjectURL(url), REVOKE_MS);
	}
</script>

{#if !hidden && (demand || resource?.error)}
	<Card title="Wärmebedarf" subtitle={demand ? `Heizsaison ${demand.season}` : undefined}>
		{#if demand && option}
			<ul class="fit">
				{#each lines as line (line)}
					<li>{line}</li>
				{/each}
			</ul>
			<EChart {option} label="Wärmebedarf je Tag, Heizsaison {demand.season}" height="18rem" />
			<p class="muted small">{acNote(demand)}</p>
			<p>{seasonTotalsText(demand)}</p>
			{#if months.length > 0}
				<table aria-label="Klimaanlage heizt, Strom je Monat">
					<thead>
						<tr>
							<th scope="col">Klimaanlage heizt, Strom je Monat</th>
							<th scope="col" class="num">Strom</th>
						</tr>
					</thead>
					<tbody>
						{#each months as row (row.month)}
							<tr>
								<td>{row.label}</td>
								<td class="num">{row.value}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
			<p><button type="button" onclick={download}>CSV herunterladen</button></p>
		{:else if resource?.error}
			<Notice level="error">{resource.error.detail}</Notice>
		{/if}
	</Card>
{/if}

<style>
	.fit {
		margin: 0 0 0.75rem;
		padding: 0;
		list-style: none;
	}

	.small {
		font-size: 0.8125rem;
	}

	table {
		width: 100%;
		margin-bottom: 0.75rem;
	}

	th,
	td {
		padding: 0.25rem 0;
		text-align: left;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
		font-weight: normal;
	}

	.num {
		text-align: right;
	}
</style>
