<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import ConsumerSummary from '$lib/dashboard/ConsumerSummary.svelte';
	import EnergyFlow from '$lib/dashboard/EnergyFlow.svelte';
	import { flowBranches, wallboxLabels } from '$lib/dashboard/flow';
	import Notices from '$lib/dashboard/Notices.svelte';
	import { noticesFrom } from '$lib/dashboard/notices';
	import SocList from '$lib/dashboard/SocList.svelte';
	import { socEntries } from '$lib/dashboard/socs';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';

	const system = new Resource((o) => api.system(o), { intervalMs: 15_000 });
	const consumers = new Resource((o) => api.consumers(o), { intervalMs: 15_000 });
	const limits = new Resource((o) => api.limits(o));

	onMount(() => {
		const stops = [system.start(), consumers.start(), limits.start()];
		return () => stops.forEach((stop) => stop());
	});

	const branches = $derived(flowBranches(live.state?.derived, wallboxLabels(limits.data)));
	const socs = $derived(socEntries(live.state, limits.data));
	const notices = $derived(noticesFrom(system.data));
</script>

<div class="dashboard">
	<div class="wide">
		<Card title="Energiefluss">
			<EnergyFlow {branches} stale={live.stale} />
		</Card>
	</div>
	<Card title="Ladestände">
		<SocList entries={socs} stale={live.stale} />
	</Card>
	<Card title="Hinweise">
		{#if system.error}
			<Notice level="error">{system.error.detail}</Notice>
		{/if}
		<Notices {notices} />
	</Card>
	<Card title="Verbraucher">
		{#if consumers.data}
			<ConsumerSummary tree={consumers.data.tree} />
		{:else if consumers.error}
			<Notice level="error">{consumers.error.detail}</Notice>
		{:else}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>
</div>

<style>
	.dashboard {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
		align-items: start;
	}

	@media (min-width: 60rem) {
		.wide {
			grid-column: span 2;
		}
	}
</style>
