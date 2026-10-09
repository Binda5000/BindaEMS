<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onUnauthorized } from '$lib/api/client';
	import { live } from '$lib/live.svelte';
	import { loginHref } from '$lib/navigation';

	let { children } = $props();

	onMount(() => {
		// Sitzung abgelaufen (401 einer Anfrage oder 4401 der Live-Verbindung): zur Anmeldung
		const expired = () => void goto(loginHref(page.url, true), { replaceState: true });
		onUnauthorized(expired);
		live.onUnauthorized = expired;
		return () => {
			onUnauthorized(null);
			live.onUnauthorized = null;
		};
	});
</script>

{@render children()}
