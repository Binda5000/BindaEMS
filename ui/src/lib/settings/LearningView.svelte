<script lang="ts">
	// Lastmodell in der Leseansicht der Einstellungen (ohne Abfrage der HA-Datenbank)
	import type { LearningSettings } from '$lib/api/schemas';

	interface Props {
		learning: LearningSettings;
	}

	let { learning }: Props = $props();
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

<style>
	.plain {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-wrap: anywhere;
	}
</style>
