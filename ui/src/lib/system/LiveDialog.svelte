<script lang="ts">
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { ControlReply, CoreHealth, Intent } from '$lib/api/schemas';
	import Dialog from '$lib/components/Dialog.svelte';
	import { actuatorDisplay } from '$lib/wallboxes';
	import { liveConditions, needsEvccConfirmation } from './view';

	interface Props {
		open: boolean;
		health: CoreHealth;
		/** Schlüssel der Wallbox → Name aus den Einstellungen */
		wallboxNames?: Record<string, string>;
		intent: Intent | null;
		onDone: (reply: ControlReply) => Promise<void>;
	}

	let { open = $bindable(false), health, intent, wallboxNames = {}, onDone }: Props = $props();

	let selected = $state<Record<string, boolean>>({});
	let protocol = $state(false);
	let evcc = $state(false);
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	// Beim Öffnen und beim Schließen (Erfolg, Abbrechen, Esc) bleibt nichts stehen, auch kein
	// Passwort; Esc oder „Abbrechen“ ändern nichts
	$effect(() => {
		void open;
		selected = {};
		protocol = false;
		evcc = false;
		password = '';
		error = null;
		busy = false;
	});

	const chosen = $derived(
		health.actuators.filter((a) => a.live_allowed && selected[a.id]).map((a) => a.id)
	);
	const needsEvcc = $derived(needsEvccConfirmation(chosen));
	const ready = $derived(
		chosen.length > 0 && protocol && (!needsEvcc || evcc) && password !== '' && !busy
	);

	/** Die Bestätigungen gelten für genau die gewählten Aktoren: ändert sich die Wahl, entfallen sie. */
	function selectionChanged() {
		protocol = false;
		evcc = false;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!ready) return;
		busy = true;
		error = null;
		let reply: ControlReply;
		try {
			const modes = Object.fromEntries(chosen.map((id) => [id, 'live' as const]));
			reply = await api.setActuators(modes, password, true);
		} catch (failure) {
			error = failure instanceof ApiError ? failure.detail : 'Unbekannter Fehler';
			password = '';
			busy = false;
			return;
		}
		busy = false; // erst dann lässt der Dialog sich schließen
		open = false;
		await onDone(reply);
	}
</script>

<Dialog bind:open {busy} title="Live schalten">
	<form onsubmit={submit} novalidate>
		<ul class="actuators">
			{#each health.actuators as actuator (actuator.id)}
				<li>
					<label class="check">
						<input
							type="checkbox"
							disabled={!actuator.live_allowed || busy}
							bind:checked={selected[actuator.id]}
							onchange={selectionChanged}
						/>
						{actuatorDisplay(actuator.id, actuator.label, wallboxNames)}
					</label>
					<ul class="conditions">
						{#each liveConditions(actuator, health, intent) as condition (condition.label)}
							<li class:unmet={!condition.met}>
								{condition.label}: {condition.met ? 'ja' : 'nein'}
							</li>
						{/each}
					</ul>
				</li>
			{/each}
		</ul>
		<p class="muted">EVCS und Tesla gehen gemeinsam live.</p>

		<label class="check">
			<input type="checkbox" bind:checked={protocol} disabled={busy} />
			Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.
		</label>
		{#if needsEvcc}
			<label class="check">
				<input type="checkbox" bind:checked={evcc} disabled={busy} />
				evcc ist ganz abgeschaltet.
			</label>
		{/if}

		<label class="field">
			Passwort
			<input type="password" autocomplete="current-password" bind:value={password} />
		</label>
		{#if error}<p class="error" role="alert">{error}</p>{/if}

		<div class="buttons">
			<button type="submit" disabled={!ready}>Live schalten</button>
			<button type="button" disabled={busy} onclick={() => (open = false)}>Abbrechen</button>
		</div>
	</form>
</Dialog>

<style>
	form {
		display: grid;
		gap: 0.5rem;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.actuators {
		display: grid;
		gap: 0.5rem;
	}

	/* Das Feld ist 44 px hoch, auch wenn das Häkchen selbst klein ist */
	.check {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
	}

	.check input {
		flex: none;
	}

	.conditions {
		display: flex;
		flex-wrap: wrap;
		gap: 0 1rem;
		padding-left: 1.75rem;
		font-size: 0.875rem;
		color: var(--muted);
	}

	.unmet {
		color: var(--text);
		font-weight: 600;
	}

	.muted,
	.error {
		margin: 0;
	}

	.field {
		display: grid;
		gap: 0.25rem;
		font-weight: 600;
	}

	.error {
		color: var(--error);
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}
</style>
