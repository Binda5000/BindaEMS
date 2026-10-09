<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { Candidates, Consumer, ConsumerInput } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { draftToInput, parentOptions, type ConsumerDraft, type DraftErrors } from './form';

	interface Props {
		draft: ConsumerDraft;
		consumers: Consumer[];
		candidates: Candidates | undefined;
		editingId: number | null;
		onSave: (input: ConsumerInput) => Promise<void>;
		onCancel: () => void;
	}

	let { draft = $bindable(), consumers, candidates, editingId, onSave, onCancel }: Props = $props();

	const uid = $props.id();
	let errors = $state<DraftErrors>({});
	let serverError = $state<string | null>(null);
	let busy = $state(false);

	const parents = $derived(parentOptions(consumers, editingId));
	const suggestions = $derived(
		draft.sourceKind === 'core'
			? (candidates?.core.map((candidate) => candidate.ref) ?? [])
			: (candidates?.ha.map((candidate) => candidate.entity_id) ?? [])
	);

	// gewählte HA-Entität aus den Vorschlägen: Einheit übernehmen
	function refChanged() {
		if (draft.sourceKind !== 'ha') return;
		const match = candidates?.ha.find((candidate) => candidate.entity_id === draft.powerRef.trim());
		if (match) draft.powerUnit = match.unit;
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

{#snippet fieldError(key: keyof DraftErrors)}
	{#if errors[key]}
		<p class="error" id="{uid}-{key}-error">{errors[key]}</p>
	{/if}
{/snippet}

<form class="consumer-form" onsubmit={submit} novalidate>
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

	<fieldset class="field">
		<legend>Quelle</legend>
		<label class="choice">
			<input type="radio" name="{uid}-source" value="core" bind:group={draft.sourceKind} />
			core-Signal
		</label>
		<label class="choice">
			<input type="radio" name="{uid}-source" value="ha" bind:group={draft.sourceKind} />
			HA-Entität
		</label>
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
			placeholder={draft.sourceKind === 'core'
				? 'load.obergeschoss.power_w'
				: 'sensor.kueche_power'}
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
		{/if}
		{@render fieldError('powerRef')}
	</div>

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
		<label for="{uid}-parent">Elternelement</label>
		<select id="{uid}-parent" bind:value={draft.parentId}>
			<option value="">– keines (direkt unter Haus) –</option>
			{#each parents as parent (parent.id)}
				<option value={String(parent.id)}>{parent.name}</option>
			{/each}
		</select>
		{@render fieldError('parentId')}
	</div>

	<div class="row">
		<div class="field">
			<label for="{uid}-color">Farbe</label>
			<input id="{uid}-color" type="color" bind:value={draft.color} />
			{@render fieldError('color')}
		</div>
		<div class="field">
			<label for="{uid}-sort">Reihenfolge</label>
			<input
				id="{uid}-sort"
				inputmode="numeric"
				bind:value={draft.sort}
				aria-invalid={errors.sort ? 'true' : undefined}
				aria-describedby={errors.sort ? `${uid}-sort-error` : undefined}
			/>
			{@render fieldError('sort')}
		</div>
	</div>

	<div class="field">
		<label for="{uid}-group">Gruppe (optional)</label>
		<input id="{uid}-group" bind:value={draft.group} maxlength="60" />
		{@render fieldError('group')}
	</div>

	<div class="buttons">
		<button type="submit" disabled={busy}>Speichern</button>
		<button type="button" onclick={onCancel}>Abbrechen</button>
	</div>
</form>

<style>
	.consumer-form {
		display: grid;
		gap: 0.75rem;
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
		margin-bottom: 0.25rem;
	}

	.choice {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-weight: 400;
		margin-right: 1rem;
	}

	.row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.75rem;
	}

	.field input:not([type='radio']),
	.field select {
		width: 100%;
		min-width: 0;
	}

	input[type='color'] {
		padding: 0.125rem;
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
		margin-top: 0.5rem;
	}
</style>
