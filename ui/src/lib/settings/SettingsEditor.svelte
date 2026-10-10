<script lang="ts">
	import { untrack } from 'svelte';
	import { ApiError, detailText } from '$lib/api/errors';
	import type { Limits, RuntimeSettings, SettingsCurrent } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { WALLBOX_NAME_MAX, WALLBOX_TYPES } from '$lib/wallboxes';
	import { applyDraft, draftFrom, editorFieldErrors } from './model';

	interface Props {
		current: SettingsCurrent;
		/** Ladestationen aus config.yaml (für ihre Namen); fehlt, solange nicht geladen */
		wallboxes?: Limits['wallboxes'];
		save: (
			baseVersion: number,
			settings: RuntimeSettings,
			comment: string | null
		) => Promise<SettingsCurrent>;
		onSaved: (result: SettingsCurrent, tariffChanged: boolean) => void;
		onReload: () => void;
		onCancel?: () => void;
	}

	let { current, wallboxes = {}, save, onSaved, onReload, onCancel }: Props = $props();

	const uid = $props.id();
	// Der Entwurf beginnt beim geladenen Stand; „Neu laden“ baut den Editor neu auf
	let draft = $state(untrack(() => draftFrom(current.settings, Object.keys(wallboxes))));
	let errors = $state<Record<string, string>>({});

	// Ladestationen, die erst nach dem Öffnen geladen wurden, bekommen ein leeres Feld
	$effect(() => {
		const keys = Object.keys(wallboxes);
		untrack(() => {
			for (const key of keys) draft.wallboxNames[key] ??= '';
		});
	});
	let message = $state<string | null>(null);
	let conflict = $state(false);
	let busy = $state(false);

	const fixed = $derived(current.settings.tariff.components.filter((c) => c.source === 'fixed'));

	function describedBy(key: string): string | undefined {
		return errors[key] ? `${uid}-${key}-error` : undefined;
	}

	function addMonth() {
		draft.feedIn.push({ month: '', ct: '' });
	}

	function removeMonth(index: number) {
		draft.feedIn.splice(index, 1);
		errors = {};
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		message = null;
		conflict = false;
		const result = applyDraft(current.settings, draft);
		if ('errors' in result) {
			errors = result.errors;
			return;
		}
		errors = {};
		busy = true;
		try {
			const saved = await save(current.version, result.settings, draft.comment.trim() || null);
			onSaved(saved, result.tariffChanged);
		} catch (error) {
			if (error instanceof ApiError && error.status === 409) {
				conflict = true;
				message = error.detail;
			} else if (error instanceof ApiError && error.status === 422) {
				const detail = (error.body as { detail?: unknown } | null)?.detail;
				const mapped = editorFieldErrors(detail, result.settings);
				const known = Object.fromEntries(
					Object.entries(mapped).filter(([key]) =>
						/^(values|feedIn|prices|wallboxNames)\./.test(key)
					)
				);
				errors = known;
				const rest = Object.entries(mapped).filter(([key]) => !(key in known));
				if (rest.length > 0) message = rest.map(([key, text]) => `${key}: ${text}`).join('\n');
				else if (Object.keys(known).length === 0) message = detailText(detail);
			} else {
				message = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
			}
		} finally {
			busy = false;
		}
	}
</script>

{#snippet fieldError(key: string)}
	{#if errors[key]}
		<p class="error" id="{uid}-{key}-error">{errors[key]}</p>
	{/if}
{/snippet}

<form class="editor" onsubmit={submit} novalidate>
	{#if message}
		<Notice level="error">{message}</Notice>
	{/if}
	{#if conflict}
		<p>
			<button type="button" onclick={onReload}>Neu laden (Eingaben verwerfen)</button>
		</p>
	{/if}

	<fieldset>
		<legend>Tarifbestandteile</legend>
		<p class="hint">Netto in ct/kWh; leer = noch kein Wert (zählt als 0).</p>
		{#each fixed as component (component.id)}
			{@const key = `values.${component.id}`}
			<div class="field">
				<label for="{uid}-{component.id}">{component.name} (ct/kWh netto)</label>
				<input
					id="{uid}-{component.id}"
					inputmode="decimal"
					bind:value={draft.values[component.id]}
					aria-invalid={errors[key] ? 'true' : undefined}
					aria-describedby={describedBy(key)}
				/>
				{@render fieldError(key)}
			</div>
		{/each}
	</fieldset>

	<fieldset>
		<legend>Einspeisung (OeMAG-Marktpreis je Monat)</legend>
		{#each draft.feedIn as row, index (index)}
			<div class="month-row">
				<div class="field">
					<label for="{uid}-month-{index}">Monat</label>
					<input
						id="{uid}-month-{index}"
						type="month"
						bind:value={row.month}
						aria-invalid={errors[`feedIn.${index}.month`] ? 'true' : undefined}
						aria-describedby={describedBy(`feedIn.${index}.month`)}
					/>
					{@render fieldError(`feedIn.${index}.month`)}
				</div>
				<div class="field">
					<label for="{uid}-ct-{index}">ct/kWh</label>
					<input
						id="{uid}-ct-{index}"
						inputmode="decimal"
						bind:value={row.ct}
						aria-invalid={errors[`feedIn.${index}.ct`] ? 'true' : undefined}
						aria-describedby={describedBy(`feedIn.${index}.ct`)}
					/>
					{@render fieldError(`feedIn.${index}.ct`)}
				</div>
				<button
					type="button"
					class="remove"
					aria-label="Monat {row.month || index + 1} entfernen"
					onclick={() => removeMonth(index)}
				>
					Entfernen
				</button>
			</div>
		{/each}
		<button type="button" onclick={addMonth}>Monat hinzufügen</button>
	</fieldset>

	<fieldset>
		<legend>Preise</legend>
		<div class="field">
			<label for="{uid}-vat-mode">Börsenpreise kommen</label>
			<select id="{uid}-vat-mode" bind:value={draft.prices.vat_mode}>
				<option value="auto">automatisch erkennen</option>
				<option value="net">netto</option>
				<option value="gross">brutto</option>
			</select>
		</div>
		<div class="field">
			<label for="{uid}-vat-fallback">Annahme, wenn nicht erkennbar</label>
			<select id="{uid}-vat-fallback" bind:value={draft.prices.vat_fallback}>
				<option value="net">netto</option>
				<option value="gross">brutto</option>
			</select>
		</div>
		<div class="field">
			<label for="{uid}-reference">Referenzquelle (Prüfung und Ersatz)</label>
			<select id="{uid}-reference" bind:value={draft.prices.reference_source}>
				<option value="energy_charts">Energy-Charts</option>
				<option value="awattar">aWATTar</option>
			</select>
		</div>
	</fieldset>

	{#if Object.keys(draft.wallboxNames).length > 0}
		<fieldset>
			<legend>Ladestationen</legend>
			<p class="hint">Eigener Name für Übersicht und Verlauf; leer = Typname.</p>
			{#each Object.keys(draft.wallboxNames) as key (key)}
				{@const field = `wallboxNames.${key}`}
				{@const wallbox = wallboxes[key]}
				<div class="field">
					<label for="{uid}-wallbox-{key}">
						{wallbox ? `${key} (${WALLBOX_TYPES[wallbox.type]})` : key}
					</label>
					<input
						id="{uid}-wallbox-{key}"
						maxlength={WALLBOX_NAME_MAX}
						placeholder={wallbox ? WALLBOX_TYPES[wallbox.type] : key}
						bind:value={draft.wallboxNames[key]}
						aria-invalid={errors[field] ? 'true' : undefined}
						aria-describedby={describedBy(field)}
					/>
					{@render fieldError(field)}
				</div>
			{/each}
		</fieldset>
	{/if}

	<div class="field">
		<label for="{uid}-comment">Kommentar (optional)</label>
		<input id="{uid}-comment" maxlength="200" bind:value={draft.comment} />
	</div>

	<div class="buttons">
		<button type="submit" disabled={busy}>Speichern</button>
		{#if onCancel}
			<button type="button" onclick={onCancel}>Abbrechen</button>
		{/if}
	</div>
</form>

<style>
	.editor {
		display: grid;
		gap: 1rem;
	}

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

	label {
		font-size: 0.9375rem;
	}

	.month-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
		align-items: end;
		gap: 0.5rem;
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
</style>
