<script lang="ts">
	// Fahrverhalten im Editor: Puffer bei der Rückkehr, Grenzen des Ziel-SOC und je Fahrzeug die
	// Anwesenheit aus Home Assistant. Die Automatik schaltet die Fahrzeugkarte, nicht dieser Editor.
	import type { DrivingDraft } from './model';

	interface Props {
		draft: DrivingDraft;
		/** Fehler mit den Schlüsseln des Editors (`learning.driving.…`) */
		errors: Record<string, string>;
		/** Namen der Fahrzeuge nach Schlüssel (aus den harten Grenzen); fehlt der Name, gilt der Schlüssel */
		vehicles?: Record<string, { name: string }>;
	}

	let { draft = $bindable(), errors, vehicles = {} }: Props = $props();

	type NumberField = 'returnReservePct' | 'targetMinPct' | 'targetMaxPct';

	const uid = $props.id();
	const PREFIX = 'learning.driving';

	/** Fehler des Feldes; für die Grenzen auch der Fehler des ganzen Abschnitts (Prüfung des Servers) */
	function errorOf(key: string): string | undefined {
		const own = errors[`${PREFIX}.${key}`];
		if (own) return own;
		return key === 'target_min_pct' ? errors[PREFIX] : undefined;
	}

	/** Hilfetext und, wenn es einen gibt, Fehlertext eines Feldes (`id` ohne Präfix der Instanz) */
	function describedBy(id: string, hasError: boolean): string {
		return hasError ? `${uid}-${id}-hint ${uid}-${id}-error` : `${uid}-${id}-hint`;
	}
</script>

{#snippet number(field: NumberField, key: string, label: string, hint: string)}
	<div class="field">
		<label for="{uid}-{key}">{label}</label>
		<input
			id="{uid}-{key}"
			inputmode="numeric"
			bind:value={draft[field]}
			aria-invalid={errorOf(key) ? 'true' : undefined}
			aria-describedby={describedBy(key, !!errorOf(key))}
		/>
		<p class="hint" id="{uid}-{key}-hint">{hint}</p>
		{#if errorOf(key)}
			<p class="error" id="{uid}-{key}-error">{errorOf(key)}</p>
		{/if}
	</div>
{/snippet}

<fieldset>
	<legend>Fahrverhalten</legend>
	{@render number(
		'returnReservePct',
		'return_reserve_pct',
		'Puffer bei der Rückkehr (%)',
		'So viel Ladestand soll nach der typischen Fahrt noch übrig sein.'
	)}
	{@render number(
		'targetMinPct',
		'target_min_pct',
		'Ziel-SOC mindestens (%)',
		'Vorschläge und Automatik bleiben in diesem Bereich.'
	)}
	{@render number(
		'targetMaxPct',
		'target_max_pct',
		'Ziel-SOC höchstens (%)',
		'Vorschläge und Automatik bleiben in diesem Bereich.'
	)}

	{#each Object.keys(draft.presenceEntities) as key (key)}
		{@const problem = errors[`${PREFIX}.presence_entities.${key}`]}
		<fieldset class="car">
			<legend>{vehicles[key]?.name ?? key}</legend>
			<div class="field">
				<label for="{uid}-presence-{key}">Anwesenheit aus Home Assistant</label>
				<input
					id="{uid}-presence-{key}"
					bind:value={draft.presenceEntities[key]}
					autocomplete="off"
					spellcheck="false"
					aria-invalid={problem ? 'true' : undefined}
					aria-describedby={describedBy(`presence-${key}`, !!problem)}
				/>
				<p class="hint" id="{uid}-presence-{key}-hint">
					Optional, etwa device_tracker.… des Autos. Leer: Tessie oder home_entity.
				</p>
				{#if problem}
					<p class="error" id="{uid}-presence-{key}-error">{problem}</p>
				{/if}
			</div>
		</fieldset>
	{/each}
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

	.car legend {
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
