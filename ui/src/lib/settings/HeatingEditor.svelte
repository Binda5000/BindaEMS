<script lang="ts">
	// Ölheizung im Editor: Kesselwerte für Wärmebedarf und Ölverbrauch aus den Brennerstunden und die
	// Leistungszahl der Klimaanlage beim Heizen. Leer = nicht eingetragen. Prüfung in `model.ts`.
	import type { HeatingDraft } from './model';

	interface Props {
		draft: HeatingDraft;
		/** Fehler mit den Schlüsseln des Editors (`heating.…`) */
		errors: Record<string, string>;
	}

	let { draft = $bindable(), errors }: Props = $props();

	type Field = 'burnerKw' | 'oilLPerH' | 'oilPriceEurL' | 'acCop';

	const uid = $props.id();
	const PREFIX = 'heating';
</script>

{#snippet number(field: Field, key: string, label: string, hint?: string)}
	{@const problem = errors[`${PREFIX}.${key}`]}
	<div class="field">
		<label for="{uid}-{key}">{label}</label>
		<input
			id="{uid}-{key}"
			inputmode="decimal"
			bind:value={draft[field]}
			aria-invalid={problem ? 'true' : undefined}
			aria-describedby={problem ? `${uid}-${key}-error` : hint ? `${uid}-${key}-hint` : undefined}
		/>
		{#if problem}
			<p class="error" id="{uid}-{key}-error">{problem}</p>
		{:else if hint}
			<p class="hint" id="{uid}-{key}-hint">{hint}</p>
		{/if}
	</div>
{/snippet}

<fieldset>
	<legend>Ölheizung</legend>
	<p class="hint">
		Aus der Brennerlaufzeit rechnet die App Wärme und Öl. Felder leer lassen heißt: nicht
		eingetragen.
	</p>
	{@render number('burnerKw', 'burner_kw', 'Nennleistung (kW)')}
	{@render number('oilLPerH', 'oil_l_per_h', 'Öl je Brennerstunde (l/h)')}
	{@render number('oilPriceEurL', 'oil_price_eur_l', 'Ölpreis (€/l, leer = ohne Euro)')}
	{@render number(
		'acCop',
		'ac_cop',
		'Leistungszahl der Klimaanlage beim Heizen',
		'Schätzung: Wärme je kWh Strom.'
	)}
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
