<script lang="ts">
	// Planung des Akkus im Editor: Netzladen, Akku ins Auto, Marge, Wirkungsgrad, Verschleiß, Restwert,
	// Trägheit, gedachter Satz für die Monatsspitze ohne Leistungspreis, Unwetter-Reserve
	import type { StormLevel } from '$lib/api/schemas';
	import { STORM_KIND_LABELS } from '$lib/plan/model';
	import { STORM_KINDS, STORM_MIN_LEVEL_LABELS, type PlanningDraft } from './model';

	interface Props {
		draft: PlanningDraft;
		/** Fehler mit den Schlüsseln des Editors (`planning.…`) */
		errors: Record<string, string>;
	}

	let { draft = $bindable(), errors }: Props = $props();

	type TextField = Exclude<
		keyof PlanningDraft,
		'gridChargeAllowed' | 'carBatteryAllowed' | 'storm'
	>;

	const LEVELS: StormLevel[] = [1, 2, 3];

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
	{@render field(
		'peakSoftCt',
		'peak_soft_ct_per_kw',
		'Spitze vermeiden ohne Leistungspreis (ct je kW und Monat)',
		'Ohne Leistungspreis meidet der Planer neue Monatsspitzen, als kostete jedes kW darüber diesen Satz; der 15-min-Wächter hält das Budget. 0 schaltet es aus.'
	)}
	<fieldset class="storm">
		<legend>Unwetter-Reserve</legend>
		<p class="hint">
			Während einer Unwetterwarnung von GeoSphere Austria hält der Planer den Akku mindestens auf
			dem Ziel und lädt ihn vorher dafür auf, zuerst mit PV, sonst mit höchstens 3 kW aus dem Netz,
			auch bei ausgeschaltetem „Netzladen erlaubt“; die USV-Reserve aus config.yaml bleibt die harte
			Untergrenze.
		</p>
		<label class="check">
			<input type="checkbox" bind:checked={draft.storm.enabled} />
			Unwetter-Reserve
		</label>
		<div class="field">
			<label for="{uid}-storm-target">Ziel-SOC (%)</label>
			<input
				id="{uid}-storm-target"
				inputmode="numeric"
				bind:value={draft.storm.targetSocPct}
				aria-invalid={errors['planning.storm.target_soc_pct'] ? 'true' : undefined}
				aria-describedby={describedBy('planning.storm.target_soc_pct')}
			/>
			{#if errors['planning.storm.target_soc_pct']}
				<p class="error" id="{uid}-planning.storm.target_soc_pct-error">
					{errors['planning.storm.target_soc_pct']}
				</p>
			{/if}
		</div>
		<div class="field">
			<label for="{uid}-storm-level">Warnstufe</label>
			<select id="{uid}-storm-level" bind:value={draft.storm.minLevel}>
				{#each LEVELS as level (level)}
					<option value={level}>{STORM_MIN_LEVEL_LABELS[level]}</option>
				{/each}
			</select>
		</div>
		<fieldset class="kinds" aria-describedby={describedBy('planning.storm.kinds')}>
			<legend>Warnarten</legend>
			{#each STORM_KINDS as kind (kind)}
				<label class="check">
					<input type="checkbox" value={kind} bind:group={draft.storm.kinds} />
					{STORM_KIND_LABELS[kind]}
				</label>
			{/each}
			{#if errors['planning.storm.kinds']}
				<p class="error" id="{uid}-planning.storm.kinds-error">{errors['planning.storm.kinds']}</p>
			{/if}
		</fieldset>
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

	.field {
		display: grid;
		gap: 0.25rem;
		min-width: 0;
	}

	.field input,
	.field select {
		width: 100%;
		max-width: 22rem;
	}

	/* die Warnarten nebeneinander, am Handy umbrechend */
	.kinds {
		display: flex;
		flex-wrap: wrap;
		gap: 0 1rem;
	}

	.kinds legend {
		font-weight: normal;
		font-size: 0.9375rem;
	}

	.kinds .error {
		flex-basis: 100%;
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
