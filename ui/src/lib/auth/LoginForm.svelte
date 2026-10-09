<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { LoginBody, User } from '$lib/api/schemas';

	interface Props {
		login: (body: LoginBody) => Promise<User>;
		onSuccess: (user: User) => void;
	}

	let { login, onSuccess }: Props = $props();

	let username = $state('');
	let password = $state('');
	let remember = $state(false);
	let totp = $state('');
	let needsTotp = $state(false); // bleibt nach `totp_required` bis zum Erfolg
	let busy = $state(false);
	let message = $state<string | null>(null);
	let codeInput = $state<HTMLInputElement>();

	$effect(() => {
		if (needsTotp) codeInput?.focus();
	});

	function totpRequired(body: unknown): boolean {
		return (
			typeof body === 'object' &&
			body !== null &&
			(body as { totp_required?: unknown }).totp_required === true
		);
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		message = null;
		try {
			const body: LoginBody = { username, password, remember };
			if (needsTotp) body.totp = totp;
			onSuccess(await login(body));
		} catch (error) {
			if (error instanceof ApiError && totpRequired(error.body) && !needsTotp) {
				needsTotp = true;
			} else {
				message = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
			}
		} finally {
			busy = false;
		}
	}
</script>

<form class="login-form" onsubmit={submit}>
	<label for="login-username">Benutzername</label>
	<input
		id="login-username"
		name="username"
		autocomplete="username"
		autocapitalize="none"
		spellcheck="false"
		required
		bind:value={username}
	/>

	<label for="login-password">Passwort</label>
	<input
		id="login-password"
		name="password"
		type="password"
		autocomplete="current-password"
		required
		bind:value={password}
	/>

	{#if needsTotp}
		<label for="login-totp">Bestätigungscode</label>
		<input
			id="login-totp"
			name="totp"
			inputmode="numeric"
			autocomplete="one-time-code"
			maxlength="16"
			required
			bind:this={codeInput}
			bind:value={totp}
		/>
		<p class="hint">Code aus der Authenticator-App (6 Ziffern).</p>
	{/if}

	<label class="check">
		<input type="checkbox" name="remember" bind:checked={remember} />
		Angemeldet bleiben <span class="hint">(gilt nicht für Admins)</span>
	</label>

	{#if message}
		<p class="error" role="alert">{message}</p>
	{/if}

	<button type="submit" disabled={busy}>Anmelden</button>
</form>

<style>
	.login-form {
		display: grid;
		gap: 0.5rem;
	}

	label:not(.check) {
		font-weight: 600;
		margin-top: 0.5rem;
	}

	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}

	.hint {
		color: var(--muted, #666);
		font-size: 0.875rem;
		margin: 0;
	}

	.error {
		color: var(--error, #b00020);
		margin: 0.5rem 0 0;
		white-space: pre-line;
	}

	button {
		margin-top: 1rem;
	}
</style>
