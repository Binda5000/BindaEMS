<script lang="ts">
	// Betrieb im Blick: Betriebscheck (alle 15 s), Auffälligkeiten der letzten 14 Tage und Schalter
	// (alle 5 min); nur lesend, gestellt wird auf den verlinkten Seiten
	import { onMount } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import CheckCard from '$lib/ops/CheckCard.svelte';
	import FindingsCard from '$lib/ops/FindingsCard.svelte';
	import SwitchesCard from '$lib/ops/SwitchesCard.svelte';
	import { Resource } from '$lib/resource.svelte';

	const FINDINGS_DAYS = 14;

	const check = new Resource((o) => api.opsCheck(o), { intervalMs: 15_000 });
	const findings = new Resource((o) => api.opsFindings(FINDINGS_DAYS, o), { intervalMs: 300_000 });
	const switches = new Resource((o) => api.opsSwitches(o), { intervalMs: 300_000 });

	// für das Jahr in „seit …“: der Zeitpunkt der letzten Ladung der Schalter
	const now = $derived.by(() => {
		void switches.data;
		return new Date();
	});

	onMount(() => {
		const stops = [check, findings, switches].map((resource) => resource.start());
		return () => stops.forEach((stop) => stop());
	});
</script>

<div class="ops">
	<CheckCard check={check.data} error={check.error} />
	<FindingsCard findings={findings.data} error={findings.error} />
	<SwitchesCard switches={switches.data} error={switches.error} {now} />
</div>

<style>
	.ops {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}
</style>
