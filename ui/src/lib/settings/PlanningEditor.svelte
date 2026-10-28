<script lang="ts">
	// Planung des Akkus im Editor: Netzladen, Akku ins Auto, Marge, Wirkungsgrad, Verschleiß, Restwert, Trägheit
	import type { PlanningDraft } from './model';

	interface Props {
		draft: PlanningDraft;
		/** Fehler mit den Schlüsseln des Editors (`planning.…`) */
		errors: Record<string, string>;
	}

	let { draft = $bindable(), errors }: Props = $props();

	type TextField = Exclude<keyof PlanningDraft, 'gridChargeAllowed' | 'carBatteryAllowed'>;

	const uid = $props.id();

	function describedBy(key: string): string | undefined {
		return errors[key] ? `${uid}-${key}-error` : undefined;
	}
</script>

{#snippet field(key: TextField, id: string, label: string, hint: string | null)}
	<div class="field">
		<label for="{uid}-{id}">{label}</label>
		<input
			id="{uid}-{id}"
			inputmode="decimal"
			bind:value={draft[key]}
			aria-invalid={errors[`planning.${id}`] ? 'true' : undefined}
			aria-describedby={describedBy(`planning.${id}`)}
		/>
		{#if hint}<p class="hint">{hint}</p>{/if}
		{#if errors[`planning.${id}`]}
			<p class="error" id="{uid}-planning.{id}-error">{errors[`planning.${id}`]}</p>
		{/if}
	</div>
{/snippet}

<fieldset>
	<legend>Planung</legend>
	<label class="check">
		<input type="checkbox" bind:checked={draft.gridChargeAllowed} />
		Netzladen erlaubt
	</label>
	<p class="hint">Der Optimierer darf den Akku zu günstigen Zeiten aus dem Netz laden.</p>
	<label class="check">
		<input type="checkbox" bind:checked={draft.carBatteryAllowed} />
		Akku ins Auto, wenn übrig
	</label>
	<p class="hint">
		Autos in Geplant und Sofort laden aus dem Akku, wenn das Haus die Energie bis zur nächsten
		PV-Füllung nicht braucht und Akkustrom billiger ist als Netzstrom.
	</p>
	{@render field(
		'marginCt',
		'margin_ct',
		'Marge (ct/kWh)',
		'Mindestgewinn je kWh, damit sich Netzladen lohnt.'
	)}
	{@render field(
		'roundtripPct',
		'roundtrip_pct',
		'Roundtrip-Wirkungsgrad (%)',
		'Laden und Entladen zusammen.'
	)}
	{@render field('wearCt', 'wear_ct', 'Verschleiß (ct/kWh)', 'Kosten je kWh Durchsatz.')}
	{@render field(
		'endValueFactor',
		'end_value_factor',
		'Vorsichtsfaktor Restwert',
		'Bewertet die Energie im Akku am Ende des Horizonts.'
	)}
	{@render field(
		'inertiaCt',
		'inertia_ct',
		'Planträgheit (ct/kWh)',
		'Kosten je kWh Abweichung vom vorigen Plan.'
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

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
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
