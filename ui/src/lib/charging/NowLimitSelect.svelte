<script lang="ts">
	import { nowLimitChoice, nowLimitOptions, type NowLimitSetup } from './view';

	interface Props {
		setup: NowLimitSetup;
		busy: boolean;
		/** schickt die Wahl; `null` ist „Volle Leistung“ */
		onChange: (maxA: number | null) => Promise<void>;
	}

	let { setup, busy, onChange }: Props = $props();

	const options = $derived(nowLimitOptions(setup.range, setup.voltage));
	// Wert der Auswahl: „full“ für volle Leistung, sonst die Stromstärke in A
	const current = $derived(String(nowLimitChoice(setup.max_a, setup.range) ?? 'full'));

	async function choose(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		await onChange(select.value === 'full' ? null : Number(select.value));
		select.value = current; // bei einer Ablehnung zurück auf den Stand des Servers
	}
</script>

<label>
	Höchststrom
	<select value={current} disabled={busy} onchange={choose}>
		{#each options as option (option.value)}
			<option value={String(option.value ?? 'full')}>{option.label}</option>
		{/each}
	</select>
</label>

<style>
	label {
		display: grid;
		gap: 0.25rem;
		font-weight: 600;
		min-width: 0;
	}

	select {
		max-width: 100%;
	}
</style>
