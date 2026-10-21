<script lang="ts">
	// Lastmodell und Fahrverhalten in der Leseansicht der Einstellungen (ohne Abfrage der HA-Datenbank)
	import type { LearningSettings } from '$lib/api/schemas';
	import { DASH, NBSP } from '$lib/format';
	import { DEFAULT_DRIVING } from './model';

	interface Props {
		learning: LearningSettings;
		/** Namen der Fahrzeuge nach Schlüssel (aus den harten Grenzen) */
		vehicles?: Record<string, { name: string }>;
	}

	let { learning, vehicles = {} }: Props = $props();

	const driving = $derived(learning.driving ?? DEFAULT_DRIVING);
	// alle Fahrzeuge der Konfiguration, dazu Einträge für Schlüssel, die sie nicht (mehr) kennt
	const cars = $derived([
		...new Set([...Object.keys(vehicles), ...Object.keys(driving.presence_entities)])
	]);
</script>

<dl class="facts">
	<dt>Lastmodell verwenden</dt>
	<dd>{learning.load_model_enabled ? 'ja' : 'nein'}</dd>
	<dt>Zusätzliche Sensoren aus Home Assistant</dt>
	<dd>
		{#if learning.ha_sensors.length > 0}
			<ul class="plain" aria-label="Gewählte Sensoren">
				{#each learning.ha_sensors as entityId (entityId)}
					<li>{entityId}</li>
				{/each}
			</ul>
		{:else}
			keine
		{/if}
	</dd>
</dl>

<h3>Fahrverhalten</h3>
<dl class="facts">
	<dt>Puffer bei der Rückkehr (%)</dt>
	<dd>{driving.return_reserve_pct}{NBSP}%</dd>
	<dt>Ziel-SOC mindestens (%)</dt>
	<dd>{driving.target_min_pct}{NBSP}%</dd>
	<dt>Ziel-SOC höchstens (%)</dt>
	<dd>{driving.target_max_pct}{NBSP}%</dd>
	{#each cars as key (key)}
		<dt>Anwesenheit aus Home Assistant ({vehicles[key]?.name ?? key})</dt>
		<dd class="entity">{driving.presence_entities[key] ?? DASH}</dd>
	{/each}
</dl>

<style>
	.plain {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-wrap: anywhere;
	}

	h3 {
		margin: 1rem 0 0.25rem;
		font-size: 0.9375rem;
		font-weight: 600;
	}

	.entity {
		overflow-wrap: anywhere;
	}
</style>
