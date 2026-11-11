<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { PvCandidates, PvSource, PvSourceInput } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import {
		SWATCHES,
		draftToInput,
		hasStrings,
		parentOptions,
		type PvDraft,
		type PvDraftErrors
	} from './form';

	interface Props {
		draft: PvDraft;
		sources: PvSource[];
		candidates: PvCandidates | undefined;
		editingId: number | null;
		onSave: (input: PvSourceInput) => Promise<void>;
		onCancel: () => void;
		onDelete: () => void;
	}

	let {
		draft = $bindable(),
		sources,
		candidates,
		editingId,
		onSave,
		onCancel,
		onDelete
	}: Props = $props();

	const uid = $props.id();
	let errors = $state<PvDraftErrors>({});
	let serverError = $state<string | null>(null);
	let busy = $state(false);

	const parents = $derived(parentOptions(sources, editingId));
	// eine Anlage mit Strings bleibt eine Anlage (der Server lehnt es sonst ab)
	const locked = $derived(hasStrings(sources, editingId));
	// eine HA-Entität kann in W und kW vorliegen (Home Assistant schreibt je Einheit eine Messgröße)
	const suggestions = $derived([
		...new Set(
			draft.sourceKind === 'core'
				? (candidates?.core ?? [])
				: (candidates?.ha.map((candidate) => candidate.entity_id) ?? [])
		)
	]);
	const customColor = $derived(!SWATCHES.some((swatch) => swatch.color === draft.color));

	// gewählte HA-Entität aus den Vorschlägen: Einheit übernehmen, wenn sie eindeutig ist
	function refChanged() {
		if (draft.sourceKind !== 'ha') return;
		const ref = draft.powerRef.trim();
		const matches = candidates?.ha.filter((candidate) => candidate.entity_id === ref) ?? [];
		if (matches.length === 1) draft.powerUnit = matches[0]!.unit;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		serverError = null;
		const result = draftToInput(draft);
		if ('errors' in result) {
			errors = result.errors;
			return;
		}
		errors = {};
		busy = true;
		try {
			await onSave(result.input);
		} catch (error) {
			serverError = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
		} finally {
			busy = false;
		}
	}
</script>

{#snippet fieldError(key: keyof PvDraftErrors)}
	{#if errors[key]}
		<p class="error" id="{uid}-{key}-error">{errors[key]}</p>
	{/if}
{/snippet}

<form class="pv-form" onsubmit={submit} novalidate>
	{#if serverError}
		<Notice level="error">{serverError}</Notice>
	{/if}

	<div class="field">
		<label for="{uid}-name">Name</label>
		<input
			id="{uid}-name"
			bind:value={draft.name}
			maxlength="60"
			aria-invalid={errors.name ? 'true' : undefined}
			aria-describedby={errors.name ? `${uid}-name-error` : undefined}
		/>
		{@render fieldError('name')}
	</div>

	<div class="field">
		<label for="{uid}-parent">Gehört zu</label>
		<select
			id="{uid}-parent"
			bind:value={draft.parentId}
			disabled={locked}
			aria-describedby="{uid}-parent-hint"
		>
			<option value="">Keine (eigene Anlage)</option>
			{#each parents as parent (parent.id)}
				<option value={String(parent.id)}>{parent.name}</option>
			{/each}
		</select>
		<p class="hint" id="{uid}-parent-hint">
			{locked
				? 'Die Anlage hat Strings und bleibt eine Anlage.'
				: 'Mit einer Anlage wird der Eintrag zum String dieser Anlage.'}
		</p>
		{@render fieldError('parentId')}
	</div>

	<fieldset class="field">
		<legend>Leistung kommt aus</legend>
		<div class="sources">
			<label class="choice">
				<input type="radio" name="{uid}-source" value="core" bind:group={draft.sourceKind} />
				Victron (core)
			</label>
			<label class="choice">
				<input type="radio" name="{uid}-source" value="ha" bind:group={draft.sourceKind} />
				Home Assistant
			</label>
		</div>
	</fieldset>

	<div class="field">
		<label for="{uid}-ref">{draft.sourceKind === 'core' ? 'core-Signal' : 'HA-Entität'}</label>
		<input
			id="{uid}-ref"
			list="{uid}-refs"
			autocapitalize="none"
			spellcheck="false"
			bind:value={draft.powerRef}
			onchange={refChanged}
			placeholder={draft.sourceKind === 'core' ? 'pv.huawei.power_w' : 'sensor.pv1_leistung'}
			aria-invalid={errors.powerRef ? 'true' : undefined}
			aria-describedby={errors.powerRef ? `${uid}-powerRef-error` : undefined}
		/>
		<datalist id="{uid}-refs">
			{#each suggestions as suggestion (suggestion)}
				<option value={suggestion}></option>
			{/each}
		</datalist>
		{#if candidates === undefined}
			<p class="hint">Suche Signale … (das kann wegen InfluxDB etwas dauern)</p>
		{:else if draft.sourceKind === 'ha' && candidates.ha_error}
			<p class="hint warn">{candidates.ha_error}</p>
		{:else if draft.sourceKind === 'ha' && candidates.ha.length === 0}
			<p class="hint">
				Keine Sensoren in W oder kW in der HA-Datenbank gefunden. Du kannst die Entität trotzdem
				eintragen.
			</p>
		{:else if draft.sourceKind === 'ha'}
			<p class="hint">Vorschläge: Leistungssensoren in W oder kW, die HA nach InfluxDB schreibt.</p>
		{:else}
			<p class="hint">Vorschläge: PV-Leistungen des core in W.</p>
		{/if}
		{@render fieldError('powerRef')}
	</div>

	<div class="row">
		{#if draft.sourceKind === 'ha'}
			<div class="field">
				<label for="{uid}-unit">Einheit</label>
				<select
					id="{uid}-unit"
					bind:value={draft.powerUnit}
					aria-invalid={errors.powerUnit ? 'true' : undefined}
					aria-describedby={errors.powerUnit ? `${uid}-powerUnit-error` : undefined}
				>
					<option value="">– wählen –</option>
					<option value="W">W</option>
					<option value="kW">kW</option>
				</select>
				{@render fieldError('powerUnit')}
			</div>
		{/if}
		<div class="field">
			<label for="{uid}-kwp">kWp <span class="optional">(optional)</span></label>
			<input
				id="{uid}-kwp"
				inputmode="decimal"
				bind:value={draft.kwp}
				placeholder="4,1"
				aria-invalid={errors.kwp ? 'true' : undefined}
				aria-describedby={errors.kwp ? `${uid}-kwp-error` : undefined}
			/>
			{@render fieldError('kwp')}
		</div>
	</div>

	<fieldset class="field">
		<legend>Farbe</legend>
		<div class="swatches">
			{#each SWATCHES as swatch (swatch.color)}
				<label class="swatch" style:--swatch={swatch.color}>
					<input
						type="radio"
						name="{uid}-color"
						value={swatch.color}
						aria-label={swatch.label}
						bind:group={draft.color}
					/>
				</label>
			{/each}
			<label class="swatch custom" class:chosen={customColor}>
				<input type="color" aria-label="Andere Farbe" bind:value={draft.color} />
			</label>
		</div>
		{@render fieldError('color')}
	</fieldset>

	<div class="field sort">
		<label for="{uid}-sort">Reihenfolge</label>
		<input
			id="{uid}-sort"
			inputmode="numeric"
			bind:value={draft.sort}
			aria-invalid={errors.sort ? 'true' : undefined}
			aria-describedby={errors.sort ? `${uid}-sort-error` : `${uid}-sort-hint`}
		/>
		<p class="hint" id="{uid}-sort-hint">Kleinere Zahlen stehen weiter oben.</p>
		{@render fieldError('sort')}
	</div>

	<div class="buttons">
		<button type="submit" disabled={busy}>Speichern</button>
		<button type="button" onclick={onCancel}>Abbrechen</button>
		{#if editingId !== null}
			<button type="button" class="delete" onclick={onDelete}>Löschen</button>
		{/if}
	</div>
</form>

<style>
	.pv-form {
		display: grid;
		gap: 0.875rem;
	}

	.field {
		display: grid;
		gap: 0.25rem;
		min-width: 0;
		margin: 0;
		padding: 0;
		border: none;
	}

	label,
	legend {
		font-weight: 600;
		font-size: 0.9375rem;
	}

	legend {
		padding: 0;
		margin-bottom: 0.375rem;
	}

	.optional {
		color: var(--muted);
		font-weight: 400;
	}

	.sources {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.5rem;
	}

	.choice {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
		padding: 0 0.75rem;
		border: 1px solid var(--border);
		border-radius: 8px;
		font-weight: 400;
	}

	.choice:has(input:checked) {
		border-color: var(--accent);
		box-shadow: inset 0 0 0 1px var(--accent);
		font-weight: 600;
	}

	.row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
		gap: 0.75rem;
	}

	.field input:not([type='radio']):not([type='color']),
	.field select {
		width: 100%;
		min-width: 0;
	}

	.sort input {
		max-width: 8rem;
	}

	.swatches {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.swatch {
		position: relative;
		display: grid;
		place-items: center;
		width: 2.75rem;
		height: 2.75rem;
		border-radius: 50%;
		background: var(--swatch);
		cursor: pointer;
	}

	/* der Kreis ist die Wahl; der Radioknopf bleibt für Tastatur und Screenreader */
	.swatch input[type='radio'] {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		margin: 0;
		opacity: 0;
		cursor: pointer;
	}

	.swatch:has(input:checked),
	.swatch.custom.chosen {
		box-shadow:
			0 0 0 3px var(--surface),
			0 0 0 5px var(--text);
	}

	.swatch:has(input:focus-visible) {
		outline: 3px solid var(--focus);
		outline-offset: 4px;
	}

	.swatch.custom {
		background: var(--surface-2);
		border: 1px dashed var(--border);
	}

	.swatch.custom input {
		width: 2rem;
		height: 2rem;
		min-height: 0;
		padding: 0;
		border: 0;
		border-radius: 50%;
		background: transparent;
		cursor: pointer;
	}

	.hint {
		margin: 0;
		color: var(--muted);
		font-size: 0.875rem;
	}

	.hint.warn {
		color: var(--warn);
	}

	.error {
		margin: 0;
		color: var(--error);
		font-size: 0.875rem;
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}

	.delete {
		margin-left: auto;
		border-color: var(--error);
		background: transparent;
		color: var(--error);
	}
</style>
