<script lang="ts">
	// Vorheizen und Vorkühlen im Editor (Plan 5c): Schalter, Komfortband je Betriebsart und
	// Mindestvorteil bei Netzbezug. Prüfung und Fehlertexte in `model.ts` (wie der Server).
	import type { ThermalPlanDraft } from './model';

	interface Props {
		draft: ThermalPlanDraft;
		/** Fehler mit den Schlüsseln des Editors (`learning.thermal.…`) */
		errors: Record<string, string>;
	}

	let { draft = $bindable(), errors }: Props = $props();

	type NumberField = 'heatUpK' | 'heatDownK' | 'coolUpK' | 'coolDownK' | 'marginCt';

	const uid = $props.id();
	const PREFIX = 'learning.thermal';

	function errorOf(key: string): string | undefined {
		return errors[`${PREFIX}.${key}`];
	}
</script>

{#snippet number(field: NumberField, key: string, label: string)}
	{@const problem = errorOf(key)}
	<div class="field">
		<label for="{uid}-{key}">{label}</label>
		<input
			id="{uid}-{key}"
			inputmode="decimal"
			bind:value={draft[field]}
			aria-invalid={problem ? 'true' : undefined}
			aria-describedby={problem ? `${uid}-${key}-error` : undefined}
		/>
		{#if problem}
			<p class="error" id="{uid}-{key}-error">{problem}</p>
		{/if}
	</div>
{/snippet}

<fieldset>
	<legend>Vorheizen und Vorkühlen</legend>
	<label class="check">
		<input type="checkbox" bind:checked={draft.enabled} aria-describedby="{uid}-enabled-hint" />
		Vorheizen und Vorkühlen
	</label>
	<p class="hint" id="{uid}-enabled-hint">
		Die Klimaanlage nutzt das Haus als Wärmespeicher. Aus: Der Zeitplan zeigt nur eine Vorschau.
	</p>
	{@render number('heatUpK', 'heat_up_k', 'Heizen: vorheizen bis (K)')}
	{@render number('heatDownK', 'heat_down_k', 'Heizen: absenken bis (K)')}
	{@render number('coolUpK', 'cool_up_k', 'Kühlen: vorkühlen bis (K)')}
	{@render number('coolDownK', 'cool_down_k', 'Kühlen: wärmer werden lassen bis (K)')}
	{@render number('marginCt', 'margin_ct', 'Mindestvorteil bei Netzbezug (ct/kWh)')}
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
