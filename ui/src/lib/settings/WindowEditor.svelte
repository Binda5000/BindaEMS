<script lang="ts">
	// Ein Zeitfenster eines Bestandteils: Monate, Wochentage, Uhrzeiten (Viertelstunden) und
	// Faktor oder fester Wert
	import { MONTHS, WEEKDAYS, type WindowDraft } from './model';

	interface Props {
		window: WindowDraft;
		errors: Record<string, string>;
		/** Schlüssel der Fehler, z. B. `tariff.components.2.windows.0` */
		prefix: string;
		/** Nummer des Fensters im Bestandteil (ab 0), für die Überschrift */
		index: number;
		onRemove: () => void;
	}

	let { window = $bindable(), errors, prefix, index, onRemove }: Props = $props();

	const uid = $props.id();

	const pad = (n: number) => String(n).padStart(2, '0');
	const QUARTERS = Array.from(
		{ length: 96 },
		(_, i) => `${pad(Math.floor(i / 4))}:${pad((i % 4) * 15)}`
	);
	const START_OPTIONS = QUARTERS.map((time) => ({ value: time, label: time }));
	// Tagesende ist 00:00 und heißt „24:00“
	const END_OPTIONS = [
		...QUARTERS.slice(1).map((time) => ({ value: time, label: time })),
		{ value: '00:00', label: '24:00' }
	];

	function toggle(list: number[], value: number) {
		const position = list.indexOf(value);
		if (position >= 0) list.splice(position, 1);
		else {
			list.push(value);
			list.sort((a, b) => a - b);
		}
	}

	const amountKey = $derived(`${prefix}.${window.mode === 'value' ? 'value_ct' : 'factor'}`);

	function describedBy(key: string): string | undefined {
		return errors[key] ? `${uid}-${key}-error` : undefined;
	}
</script>

{#snippet fieldError(key: string)}
	{#if errors[key]}
		<p class="error" id="{uid}-{key}-error">{errors[key]}</p>
	{/if}
{/snippet}

<fieldset class="window">
	<legend>Zeitfenster {index + 1}</legend>
	{@render fieldError(prefix)}

	<div class="field">
		<span id="{uid}-months">Monate</span>
		<div class="toggles" role="group" aria-labelledby="{uid}-months">
			{#each MONTHS as name, position (position)}
				<button
					type="button"
					class="toggle"
					aria-pressed={window.months.includes(position + 1)}
					onclick={() => toggle(window.months, position + 1)}
				>
					{name}
				</button>
			{/each}
		</div>
		{@render fieldError(`${prefix}.months`)}
	</div>

	<div class="field">
		<span id="{uid}-weekdays">Wochentage</span>
		<div class="toggles" role="group" aria-labelledby="{uid}-weekdays">
			{#each WEEKDAYS as name, position (position)}
				<button
					type="button"
					class="toggle"
					aria-pressed={window.weekdays.includes(position)}
					onclick={() => toggle(window.weekdays, position)}
				>
					{name}
				</button>
			{/each}
		</div>
		{@render fieldError(`${prefix}.weekdays`)}
	</div>

	<div class="times">
		<div class="field">
			<label for="{uid}-start">von</label>
			<select
				id="{uid}-start"
				bind:value={window.start}
				aria-invalid={errors[`${prefix}.start`] ? 'true' : undefined}
				aria-describedby={describedBy(`${prefix}.start`)}
			>
				{#each START_OPTIONS as option (option.label)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
			{@render fieldError(`${prefix}.start`)}
		</div>
		<div class="field">
			<label for="{uid}-end">bis</label>
			<select
				id="{uid}-end"
				bind:value={window.end}
				aria-invalid={errors[`${prefix}.end`] ? 'true' : undefined}
				aria-describedby={describedBy(`${prefix}.end`)}
			>
				{#each END_OPTIONS as option (option.label)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
			{@render fieldError(`${prefix}.end`)}
		</div>
	</div>

	<div class="modes">
		<label class="check">
			<input type="radio" name="{uid}-mode" value="factor" bind:group={window.mode} />
			Faktor
		</label>
		<label class="check">
			<input type="radio" name="{uid}-mode" value="value" bind:group={window.mode} />
			fester Wert (ct/kWh netto)
		</label>
	</div>
	<div class="field">
		<label for="{uid}-amount">Wert</label>
		<input
			id="{uid}-amount"
			inputmode="decimal"
			bind:value={window.amount}
			aria-invalid={errors[amountKey] ? 'true' : undefined}
			aria-describedby={describedBy(amountKey)}
		/>
		{@render fieldError(amountKey)}
	</div>

	<div class="buttons">
		<button type="button" onclick={onRemove}>Zeitfenster entfernen</button>
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

	.toggles {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
	}

	.toggle {
		padding: 0.375rem 0.5rem;
		min-width: 2.75rem;
	}

	.toggle[aria-pressed='true'] {
		border-color: var(--accent);
		background: var(--accent);
		color: var(--accent-text);
	}

	.times {
		display: grid;
		gap: 0.75rem;
	}

	.modes {
		display: grid;
		gap: 0.25rem;
	}

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.75rem;
	}

	.error {
		margin: 0;
		color: var(--error);
		font-size: 0.875rem;
	}

	@media (min-width: 40rem) {
		.times {
			grid-template-columns: repeat(2, minmax(0, 11rem));
		}
	}
</style>
