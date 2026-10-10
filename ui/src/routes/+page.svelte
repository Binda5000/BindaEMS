<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import EChart from '$lib/charts/EChart.svelte';
	import { priceForecastOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import ConsumerSummary from '$lib/dashboard/ConsumerSummary.svelte';
	import EnergyFlow from '$lib/dashboard/EnergyFlow.svelte';
	import { flowBranches, flowConsumers, wallboxLabels } from '$lib/dashboard/flow';
	import Notices from '$lib/dashboard/Notices.svelte';
	import { noticesFrom } from '$lib/dashboard/notices';
	import PriceNow from '$lib/dashboard/PriceNow.svelte';
	import { forecastForChart } from '$lib/dashboard/prices';
	import SocList from '$lib/dashboard/SocList.svelte';
	import { socEntries } from '$lib/dashboard/socs';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { theme } from '$lib/theme.svelte';

	const system = new Resource((o) => api.system(o), { intervalMs: 15_000 });
	const consumers = new Resource((o) => api.consumers(o), { intervalMs: 15_000 });
	const limits = new Resource((o) => api.limits(o), { retryMs: 30_000 });
	const pricesNow = new Resource((o) => api.pricesNow(o), { intervalMs: 60_000 });
	const prices = new Resource((o) => api.prices(null, o), { intervalMs: 300_000 });
	const forecast = new Resource((o) => api.forecast(o), { intervalMs: 300_000 });

	let nowIso = $state(new Date().toISOString()); // „jetzt“ im Diagramm, jede Minute weiter

	onMount(() => {
		const stops = [system, consumers, limits, pricesNow, prices, forecast].map((r) => r.start());
		const clock = setInterval(() => (nowIso = new Date().toISOString()), 60_000);
		return () => {
			stops.forEach((stop) => stop());
			clearInterval(clock);
		};
	});

	const branches = $derived(flowBranches(live.state?.derived, wallboxLabels(limits.data)));
	const flowItems = $derived(flowConsumers(consumers.data?.tree));
	const socs = $derived(socEntries(live.state, limits.data));
	const notices = $derived(noticesFrom(system.data));

	// Farben neu lesen, wenn hell/dunkel wechselt
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});

	const forecastSlots = $derived(forecastForChart(forecast.data?.slots ?? [], new Date(nowIso)));

	const chartOption = $derived(
		priceForecastOption(prices.data?.slots ?? [], forecastSlots, nowIso, palette)
	);
	const chartError = $derived(prices.error ?? forecast.error);
</script>

<div class="dashboard">
	<div class="wide">
		<Card title="Energiefluss">
			<EnergyFlow {branches} consumers={flowItems} stale={live.stale} />
		</Card>
	</div>
	<Card title="Strompreis">
		<PriceNow data={pricesNow.data} error={pricesNow.error} />
	</Card>
	<Card title="Ladestände">
		<SocList entries={socs} stale={live.stale} error={limits.error} />
	</Card>
	<div class="wide">
		<Card title="Preise und PV heute/morgen">
			{#if chartError}
				<Notice level="error">{chartError.detail}</Notice>
			{/if}
			<EChart
				option={chartOption}
				label="Diagramm: Bezugspreis, Einspeisung und PV-Prognose für heute und morgen"
			/>
		</Card>
	</div>
	<Card title="Hinweise">
		{#if system.error}
			<Notice level="error">{system.error.detail}</Notice>
		{/if}
		<Notices {notices} />
	</Card>
	<Card title="Verbraucher">
		<ConsumerSummary tree={consumers.data?.tree} error={consumers.error} />
	</Card>
</div>

<style>
	.dashboard {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
		align-items: start;
	}

	.wide {
		min-width: 0;
	}

	@media (min-width: 60rem) {
		.wide {
			grid-column: span 2;
		}
	}
</style>
