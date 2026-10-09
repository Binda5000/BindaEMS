<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import type { LoginBody } from '$lib/api/schemas';
	import LoginForm from '$lib/auth/LoginForm.svelte';
	import { safeNext } from '$lib/navigation';

	const next = $derived(page.url.searchParams.get('next'));
	const expired = $derived(page.url.searchParams.get('abgelaufen') === '1');

	const login = (body: LoginBody) => api.login(body).then((response) => response.user);

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
	{#if expired}
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
