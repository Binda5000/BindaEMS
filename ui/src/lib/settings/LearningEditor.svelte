<script lang="ts">
	// Lastmodell im Editor: Schalter und zusätzliche HA-Sensoren, gewählt aus den Entitäten der
	// HA-Datenbank (Entscheidung 14). Die Liste wird einmal beim Öffnen geladen.
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api/errors';
	import type { SensorCandidates } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { MAX_HA_SENSORS, type LearningDraft } from './model';

	interface Props {
		draft: LearningDraft;
		/** Fehler mit den Schlüsseln des Editors (`learning.…`) */
		errors: Record<string, string>;
		/** holt die Kandidaten; fehlt es, bleibt nur die Liste der gewählten Sensoren */
		loadCandidates?: () => Promise<SensorCandidates>;
	}

	let { draft = $bindable(), errors, loadCandidates }: Props = $props();

	/** So viele Treffer zeigt die Auswahl höchstens; der Rest erscheint nach dem Eingrenzen */
	const SHOWN_MAX = 100;

	const uid = $props.id();

	let candidates = $state.raw<SensorCandidates | null>(null);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let search = $state('');

	onMount(() => {
		if (!loadCandidates) return;
		loading = true;
		loadCandidates()
			.then((result) => (candidates = result))
			.catch((error: unknown) => {
				loadError = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
			})
			.finally(() => (loading = false));
	});

	const full = $derived(draft.haSensors.length >= MAX_HA_SENSORS);
	const reason = $derived(loadError ?? candidates?.error ?? null);
	const entities = $derived(candidates?.entities ?? []);
	const known = $derived(new Set(entities.map((entity) => entity.entity_id)));
	// Ob ein Sensor in HA fehlt, weiß man nur bei geladener Liste
	const listLoaded = $derived(candidates !== null && candidates.error === null);
	const matches = $derived.by(() => {
		const needle = search.trim().toLowerCase();
		return entities.filter(
			(entity) =>
				needle === '' ||
				entity.entity_id.toLowerCase().includes(needle) ||
				(entity.unit ?? '').toLowerCase().includes(needle)
		);
	});
	const shown = $derived(matches.slice(0, SHOWN_MAX));

	function toggle(entityId: string, selected: boolean) {
		if (selected) {
			if (!draft.haSensors.includes(entityId) && !full) draft.haSensors.push(entityId);
		} else {
			remove(entityId);
		}
	}

	function remove(entityId: string) {
		draft.haSensors = draft.haSensors.filter((id) => id !== entityId);
	}
</script>

<fieldset>
	<legend>Lastmodell</legend>
	<label class="check">
		<input type="checkbox" bind:checked={draft.loadModelEnabled} />
		Lastmodell verwenden
	</label>
	<p class="hint">Aus: Die Planung nutzt immer das Profil. Das Modell rechnet weiter mit.</p>

	<fieldset class="sensors" aria-describedby="{uid}-sensors-hint">
		<legend>Zusätzliche Sensoren aus Home Assistant (höchstens {MAX_HA_SENSORS})</legend>
		<p class="hint" id="{uid}-sensors-hint">
			Zum Beispiel Anwesenheit (person.…) oder die gemessene Außentemperatur. Wirkt ab dem nächsten
			Training.
		</p>

		{#if draft.haSensors.length > 0}
			<ul class="chosen" aria-label="Gewählte Sensoren">
				{#each draft.haSensors as entityId, index (entityId)}
					<li>
						<span class="id">{entityId}</span>
						{#if listLoaded && !known.has(entityId)}
							<span class="missing">nicht in HA gefunden</span>
						{/if}
						<button
							type="button"
							class="remove"
							aria-label="Sensor {entityId} entfernen"
							onclick={() => remove(entityId)}
						>
							Entfernen
						</button>
						{#if errors[`learning.ha_sensors.${index}`]}
							<p class="error">{errors[`learning.ha_sensors.${index}`]}</p>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="hint">Noch kein Sensor gewählt.</p>
		{/if}
		{#if errors['learning.ha_sensors']}
			<p class="error">{errors['learning.ha_sensors']}</p>
		{/if}

		{#if loading}
			<p class="hint">Sensoren werden geladen …</p>
		{:else if reason}
			<Notice level="warning">{reason}</Notice>
		{:else if candidates}
			{#if full}
				<p class="hint">
					Es sind {MAX_HA_SENSORS} Sensoren gewählt, das ist die Obergrenze. Zum Wählen weiterer zuerst
					einen entfernen.
				</p>
			{/if}
			<div class="field">
				<label for="{uid}-search">Sensoren suchen</label>
				<input id="{uid}-search" type="search" bind:value={search} />
			</div>
			<div class="candidates" role="group" aria-label="Sensoren aus Home Assistant">
				{#each shown as entity (entity.entity_id)}
					{@const selected = draft.haSensors.includes(entity.entity_id)}
					<label class="check">
						<input
							type="checkbox"
							checked={selected}
							disabled={full && !selected}
							onchange={(event) => toggle(entity.entity_id, event.currentTarget.checked)}
						/>
						{entity.entity_id}{entity.unit ? ` (${entity.unit})` : ''}
					</label>
				{:else}
					<p class="hint">Keine Entität gefunden.</p>
				{/each}
			</div>
			{#if matches.length > shown.length}
				<p class="hint">
					{matches.length - shown.length} weitere Treffer; den Suchbegriff eingrenzen.
				</p>
			{/if}
		{/if}
	</fieldset>
</fieldset>

<style>
	fieldset {
		display: grid;
		gap: 0.75rem;
		margin: 0;
		padding: 0.75rem;
		border: 1px solid var(--border);
		border-radius: 8px;
		min-width: 0;
	}

	legend {
		padding: 0 0.25rem;
		font-weight: 600;
	}

	.sensors legend {
		font-weight: 500;
	}

	.field {
		display: grid;
		gap: 0.25rem;
		min-width: 0;
	}

	.field input {
		width: 100%;
		max-width: 22rem;
	}

	label {
		font-size: 0.9375rem;
	}

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
		overflow-wrap: anywhere;
	}

	.chosen {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.25rem;
	}

	.chosen li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}

	.id {
		overflow-wrap: anywhere;
	}

	.missing {
		color: var(--warn);
		font-size: 0.875rem;
	}

	.candidates {
		display: grid;
		max-height: 16rem;
		overflow-y: auto;
		padding-inline: 0.25rem;
		border: 1px solid var(--border);
		border-radius: 8px;
	}

	.hint {
		margin: 0;
		color: var(--muted);
		font-size: 0.875rem;
	}

	.error {
		margin: 0;
		color: var(--error);
		font-size: 0.875rem;
	}
</style>
