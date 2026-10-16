<script lang="ts">
	// Tarif im Editor: alle Bestandteile mit Zeitfenstern und Gültigkeit, dazu USt. und Fixtarif
	import {
		KIND_LABEL,
		NEW_COMPONENT_NAME,
		newComponent,
		newWindow,
		type ComponentKind,
		type TariffDraft
	} from './model';
	import WindowEditor from './WindowEditor.svelte';

	interface Props {
		draft: TariffDraft;
		/** Fehler mit den Schlüsseln des Editors (`tariff.…`) */
		errors: Record<string, string>;
		/** nach Hinzufügen oder Entfernen: die Nummern in `errors` passen dann nicht mehr */
		onStructure?: () => void;
	}

	let { draft = $bindable(), errors, onStructure }: Props = $props();

	const uid = $props.id();
	const KINDS = Object.keys(KIND_LABEL) as ComponentKind[];
	let newKind = $state<ComponentKind>('energy_per_kwh');

	function describedBy(key: string): string | undefined {
		return errors[key] ? `${uid}-${key}-error` : undefined;
	}

	function addComponent() {
		draft.components.push(
			newComponent(
				newKind,
				NEW_COMPONENT_NAME[newKind],
				draft.components.map((component) => component.id)
			)
		);
		onStructure?.();
	}

	function removeComponent(index: number) {
		draft.components.splice(index, 1);
		onStructure?.();
	}

	function addWindow(index: number) {
		draft.components[index].windows.push(newWindow());
		onStructure?.();
	}

	function removeWindow(index: number, position: number) {
		draft.components[index].windows.splice(position, 1);
		onStructure?.();
	}
</script>

{#snippet fieldError(key: string)}
	{#if errors[key]}
		<p class="error" id="{uid}-{key}-error">{errors[key]}</p>
	{/if}
{/snippet}

<fieldset>
	<legend>Tarifbestandteile</legend>
	<p class="hint">Netto; leer = noch kein Wert (zählt als 0). Zahlen mit Komma oder Punkt.</p>
	{@render fieldError('tariff')}
	{@render fieldError('tariff.components')}

	{#each draft.components as component, index (component.id)}
		{@const p = `tariff.components.${index}`}
		{@const spot = component.kind === 'energy_per_kwh' && component.source === 'spot'}
		{@const withWindows =
			component.kind === 'energy_per_kwh' || component.kind === 'feed_in_per_kwh'}
		<fieldset class="component">
			<legend>{component.name.trim() || KIND_LABEL[component.kind]}</legend>
			<p class="hint">{KIND_LABEL[component.kind]}</p>
			{@render fieldError(p)}

			<div class="field">
				<label for="{uid}-{index}-name">Name</label>
				<input
					id="{uid}-{index}-name"
					bind:value={component.name}
					aria-invalid={errors[`${p}.name`] ? 'true' : undefined}
					aria-describedby={describedBy(`${p}.name`)}
				/>
				{@render fieldError(`${p}.name`)}
			</div>

			{#if component.kind === 'power_per_kw_year'}
				<div class="field">
					<label for="{uid}-{index}-value">bis zur Stufe (€/kW/Jahr netto)</label>
					<input
						id="{uid}-{index}-value"
						inputmode="decimal"
						bind:value={component.value}
						aria-invalid={errors[`${p}.value_eur`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.value_eur`)}
					/>
					{@render fieldError(`${p}.value_eur`)}
				</div>
				<div class="field">
					<label for="{uid}-{index}-above">über der Stufe (€/kW/Jahr netto)</label>
					<input
						id="{uid}-{index}-above"
						inputmode="decimal"
						bind:value={component.valueAbove}
						aria-invalid={errors[`${p}.value_above_eur`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.value_above_eur`)}
					/>
					<p class="hint">Leer = derselbe Preis wie bis zur Stufe.</p>
					{@render fieldError(`${p}.value_above_eur`)}
				</div>
				<div class="field">
					<label for="{uid}-{index}-tier">Stufe (kW)</label>
					<input
						id="{uid}-{index}-tier"
						inputmode="decimal"
						bind:value={component.tierKw}
						aria-invalid={errors[`${p}.tier_kw`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.tier_kw`)}
					/>
					{@render fieldError(`${p}.tier_kw`)}
				</div>
				<div class="field">
					<label for="{uid}-{index}-minimum">Mindestverrechnung (kW)</label>
					<input
						id="{uid}-{index}-minimum"
						inputmode="decimal"
						bind:value={component.minimumKw}
						aria-invalid={errors[`${p}.minimum_kw`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.minimum_kw`)}
					/>
					{@render fieldError(`${p}.minimum_kw`)}
				</div>
			{:else if spot}
				<p class="hint">kommt aus der Preisquelle</p>
			{:else}
				{@const key = `${p}.${component.kind === 'fixed_per_month' ? 'value_eur' : 'value_ct'}`}
				<div class="field">
					<label for="{uid}-{index}-value">Wert netto</label>
					<div class="with-unit">
						<input
							id="{uid}-{index}-value"
							inputmode="decimal"
							bind:value={component.value}
							aria-invalid={errors[key] ? 'true' : undefined}
							aria-describedby={describedBy(key)}
						/>
						<span class="unit">{component.kind === 'fixed_per_month' ? '€/Monat' : 'ct/kWh'}</span>
					</div>
					{@render fieldError(key)}
				</div>
			{/if}

			<label class="check">
				<input type="checkbox" bind:checked={component.vat} />
				mit USt.
			</label>

			<div class="dates">
				<div class="field">
					<label for="{uid}-{index}-from">gültig ab</label>
					<input
						id="{uid}-{index}-from"
						type="date"
						bind:value={component.validFrom}
						aria-invalid={errors[`${p}.valid_from`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.valid_from`)}
					/>
					{@render fieldError(`${p}.valid_from`)}
				</div>
				<div class="field">
					<label for="{uid}-{index}-until">gültig bis</label>
					<input
						id="{uid}-{index}-until"
						type="date"
						bind:value={component.validUntil}
						aria-invalid={errors[`${p}.valid_until`] ? 'true' : undefined}
						aria-describedby={describedBy(`${p}.valid_until`)}
					/>
					{@render fieldError(`${p}.valid_until`)}
				</div>
			</div>
			<p class="hint">Leer = ohne Grenze.</p>

			{#if withWindows}
				<div class="windows">
					<p class="subhead">Zeitfenster</p>
					{#each component.windows.keys() as position (position)}
						<WindowEditor
							bind:window={component.windows[position]}
							{errors}
							prefix="{p}.windows.{position}"
							index={position}
							onRemove={() => removeWindow(index, position)}
						/>
					{/each}
					<div class="buttons">
						<button type="button" onclick={() => addWindow(index)}>Zeitfenster hinzufügen</button>
					</div>
				</div>
			{/if}

			{#if !spot}
				<div class="buttons">
					<button type="button" class="remove" onclick={() => removeComponent(index)}>
						Bestandteil entfernen
					</button>
				</div>
			{/if}
		</fieldset>
	{/each}

	<div class="add">
		<div class="field">
			<label for="{uid}-kind">Art</label>
			<select id="{uid}-kind" bind:value={newKind}>
				{#each KINDS as kind (kind)}
					<option value={kind}>{KIND_LABEL[kind]}</option>
				{/each}
			</select>
		</div>
		<button type="button" onclick={addComponent}>Bestandteil hinzufügen</button>
	</div>
</fieldset>

<fieldset>
	<legend>Umsatzsteuer und Vergleich</legend>
	<div class="field">
		<label for="{uid}-vat">USt. (%)</label>
		<input
			id="{uid}-vat"
			inputmode="decimal"
			bind:value={draft.vatPct}
			aria-invalid={errors['tariff.vat_pct'] ? 'true' : undefined}
			aria-describedby={describedBy('tariff.vat_pct')}
		/>
		{@render fieldError('tariff.vat_pct')}
	</div>
	<div class="field">
		<label for="{uid}-fixed">Fixtarif zum Vergleich (ct/kWh brutto)</label>
		<input
			id="{uid}-fixed"
			inputmode="decimal"
			bind:value={draft.fixedPriceCt}
			aria-invalid={errors['tariff.fixed_price_gross_ct'] ? 'true' : undefined}
			aria-describedby={describedBy('tariff.fixed_price_gross_ct')}
		/>
		{@render fieldError('tariff.fixed_price_gross_ct')}
	</div>
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

	.with-unit {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.with-unit input {
		flex: 1 1 auto;
		min-width: 0;
	}

	.unit {
		color: var(--muted);
		font-size: 0.875rem;
		white-space: nowrap;
	}

	.dates {
		display: grid;
		gap: 0.75rem;
	}

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
	}

	.windows {
		display: grid;
		gap: 0.75rem;
	}

	.subhead {
		margin: 0;
		font-weight: 600;
	}

	.add {
		display: grid;
		gap: 0.5rem;
		justify-items: start;
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

	.buttons {
		display: flex;
		gap: 0.5rem;
	}

	@media (min-width: 40rem) {
		.dates {
			grid-template-columns: repeat(2, minmax(0, 12rem));
		}
	}
</style>
