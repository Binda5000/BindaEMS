<script lang="ts">
	import { page } from '$app/state';

	const message = $derived(
		page.status === 403
			? 'Keine Berechtigung'
			: page.status === 404
				? 'Seite nicht gefunden'
				: (page.error?.message ?? 'Unbekannter Fehler')
	);
</script>

<svelte:head>
	<title>{message} – BindaEMS</title>
</svelte:head>

<main class="error-page">
	<h1>{message}</h1>
	<p><a href="/">Zur Übersicht</a></p>
</main>

<style>
	.error-page {
		max-width: 40rem;
		margin: 10vh auto 0;
		padding: 0 1rem;
	}
</style>
