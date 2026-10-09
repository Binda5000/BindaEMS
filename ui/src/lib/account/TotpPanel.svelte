<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { TotpSetup } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { groupSecret, qrDataUrl } from './qr';

	interface Props {
		enabled: boolean;
		setup: (password: string) => Promise<TotpSetup>;
		enable: (code: string) => Promise<void>;
		disable: (password: string) => Promise<void>;
		onChanged: () => void;
	}

	let { enabled, setup, enable, disable, onChanged }: Props = $props();

	const uid = $props.id();
	let step = $state<'idle' | 'password' | 'scan' | 'disable'>('idle');
	let password = $state('');
	let code = $state('');
	// das Geheimnis bleibt in der Komponente: kein Speicher, kein Log
	let pending = $state.raw<TotpSetup | null>(null);
	let message = $state<string | null>(null);
	let busy = $state(false);

	function reset() {
		step = 'idle';
		password = '';
		code = '';
		pending = null;
	}

	function errorText(error: unknown): string {
		return error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
	}

	async function run(action: () => Promise<void>) {
		message = null;
		busy = true;
		try {
			await action();
		} catch (error) {
			message = errorText(error);
		} finally {
			busy = false;
		}
	}

	function begin(event: SubmitEvent) {
		event.preventDefault();
		void run(async () => {
			pending = await setup(password);
			password = '';
			step = 'scan';
		});
	}

	function activate(event: SubmitEvent) {
		event.preventDefault();
		void run(async () => {
			await enable(code.trim());
			reset();
			onChanged();
		});
	}

	function switchOff(event: SubmitEvent) {
		event.preventDefault();
		void run(async () => {
			await disable(password);
			reset();
			onChanged();
		});
	}
</script>

<div class="totp">
	<p class="state">
		Zwei-Faktor-Anmeldung ist <strong>{enabled ? 'aktiv' : 'aus'}</strong>.
	</p>

	{#if message}
		<Notice level="error">{message}</Notice>
	{/if}

	{#if step === 'idle'}
		{#if enabled}
			<p><button type="button" onclick={() => (step = 'disable')}>Abschalten</button></p>
		{:else}
			<p><button type="button" onclick={() => (step = 'password')}>Einrichten</button></p>
		{/if}
	{:else if step === 'password'}
		<form onsubmit={begin} novalidate>
			<label for="{uid}-password">Passwort</label>
			<input
				id="{uid}-password"
				type="password"
				autocomplete="current-password"
				bind:value={password}
			/>
			<div class="buttons">
				<button type="submit" disabled={busy}>Weiter</button>
				<button type="button" onclick={reset}>Abbrechen</button>
			</div>
		</form>
	{:else if step === 'scan' && pending}
		<form onsubmit={activate} novalidate>
			<p>Mit der Authenticator-App scannen oder das Geheimnis abtippen:</p>
			<img
				class="qr"
				src={qrDataUrl(pending.uri)}
				alt="QR-Code für die Authenticator-App"
				width="200"
				height="200"
			/>
			<code class="secret">{groupSecret(pending.secret)}</code>
			<label for="{uid}-code">Bestätigungscode</label>
			<input
				id="{uid}-code"
				inputmode="numeric"
				autocomplete="one-time-code"
				maxlength="16"
				bind:value={code}
			/>
			<div class="buttons">
				<button type="submit" disabled={busy}>Aktivieren</button>
				<button type="button" onclick={reset}>Abbrechen</button>
			</div>
		</form>
	{:else if step === 'disable'}
		<form onsubmit={switchOff} novalidate>
			<label for="{uid}-password">Passwort</label>
			<input
				id="{uid}-password"
				type="password"
				autocomplete="current-password"
				bind:value={password}
			/>
			<div class="buttons">
				<button type="submit" class="danger" disabled={busy}>
					Zwei-Faktor-Anmeldung abschalten
				</button>
				<button type="button" onclick={reset}>Abbrechen</button>
			</div>
		</form>
	{/if}
</div>

<style>
	.totp,
	form {
		display: grid;
		gap: 0.5rem;
		max-width: 24rem;
	}

	p {
		margin: 0;
	}

	.qr {
		width: 200px;
		height: 200px;
		background: #fff;
		border-radius: 8px;
	}

	.secret {
		font-size: 1.125rem;
		letter-spacing: 0.05em;
		user-select: all;
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.25rem;
	}

	.danger {
		border-color: var(--error);
		background: var(--error);
		color: #fff;
	}
</style>
