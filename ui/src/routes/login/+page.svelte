<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { LoginBody, User } from '$lib/api/schemas';
	import LoginForm from '$lib/auth/LoginForm.svelte';
	import { safeNext } from '$lib/navigation';

	const next = $derived(page.url.searchParams.get('next'));
	const expired = $derived(page.url.searchParams.get('abgelaufen') === '1');

	// gewollte Abmeldung nach einer Änderung am eigenen Konto (Seite „Benutzer“)
	const REASONS: Record<string, string> = {
		rolle: 'Deine Rolle wurde geändert – bitte neu anmelden.',
		passwort: 'Dein Passwort wurde neu gesetzt – bitte neu anmelden.'
	};
	const reason = $derived(REASONS[page.url.searchParams.get('grund') ?? ''] ?? null);

	const COOKIE_DROPPED =
		'Anmeldung angenommen, aber der Browser hat das Sitzungs-Cookie verworfen. Das UI braucht ' +
		'HTTPS (Reverse Proxy, Betriebshandbuch Abschnitt 6). Nur für einen Test im LAN ohne HTTPS: ' +
		'app.cookie_secure: false.';

	// Das Sitzungs-Cookie trägt „Secure“: über http:// verwirft der Browser es still. Ohne diese
	// Prüfung führte die nächste Seite gleich wieder zur Anmeldung, ohne jeden Hinweis.
	async function login(body: LoginBody): Promise<User> {
		const { user } = await api.login(body);
		try {
			await api.me({ redirectOn401: false });
		} catch (error) {
			if (error instanceof ApiError && error.status === 401)
				throw new ApiError(401, COOKIE_DROPPED);
			throw error;
		}
		return user;
	}

	function onSuccess() {
		void goto(safeNext(next), { replaceState: true, invalidateAll: true });
	}
</script>

<svelte:head>
	<title>Anmelden – BindaEMS</title>
</svelte:head>

<main class="login">
	<h1>Anmelden</h1>
	<p class="brand">BindaEMS</p>
	{#if reason}
		<p class="expired" role="status">{reason}</p>
	{:else if expired}
		<p class="expired" role="status">Sitzung abgelaufen – bitte neu anmelden.</p>
	{/if}
	<LoginForm {login} {onSuccess} />
</main>

<style>
	.login {
		max-width: 22rem;
		margin: 10vh auto 0;
		padding: 0 1rem;
	}

	.brand {
		margin-top: -0.5rem;
		color: var(--muted, #666);
	}

	.expired {
		padding: 0.5rem 0.75rem;
		border-left: 4px solid var(--warning, #b26a00);
		background: var(--warning-bg, #fff4e5);
	}
</style>
