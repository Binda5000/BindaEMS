<script lang="ts">
	import { SESSIONS_LIMIT } from '$lib/api/endpoints';
	import type { ChargingSession } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { SESSIONS_PAGE, sessionRows, sessionsCsv } from './view';

	interface Props {
		/** `undefined` bis zur ersten Antwort */
		sessions: ChargingSession[] | undefined;
		/** Meldung, wenn die Abfrage scheiterte */
		error?: string;
		/** Dateiname des CSV, z. B. `ladesessions-2026-10-01-2026-10-31-tesla.csv` */
		filename: string;
	}

	let { sessions, error, filename }: Props = $props();

	const rows = $derived(sessions ? sessionRows(sessions) : []);

	// So viele Zeilen stehen da. Ein anderer Zeitraum oder ein anderes Auto (anderer Dateiname)
	// beginnt wieder bei der ersten Seite; das minütliche Nachladen derselben Liste nicht.
	let shown = $state(SESSIONS_PAGE);
	const selection = $derived(filename); // gleicher Name: kein neuer Wert, der Effekt läuft nicht
	$effect(() => {
		void selection;
		shown = SESSIONS_PAGE;
	});

	// Alle geladenen Sessions (nicht nur die angezeigten) als Datei speichern, ganz im Browser
	function download() {
		if (!sessions) return;
		const url = URL.createObjectURL(
			new Blob([sessionsCsv(sessions)], { type: 'text/csv;charset=utf-8' })
		);
		const link = document.createElement('a');
		link.href = url;
		link.download = filename;
		link.click();
		URL.revokeObjectURL(url);
	}
</script>

<Card title="Ladesessions">
	{#if !sessions}
		{#if error}
			<Notice level="error">{error}</Notice>
		{:else}
			<p class="muted">Lädt …</p>
		{/if}
	{:else if rows.length === 0}
		<p class="muted">Keine Ladesessions im Zeitraum.</p>
	{:else}
		<div class="scroll wide">
			<table aria-label="Ladesessions">
				<thead>
					<tr>
						<th scope="col">Beginn</th>
						<th scope="col">Ende</th>
						<th scope="col">Wallbox</th>
						<th scope="col">Auto</th>
						<th scope="col" class="num">Energie</th>
						<th scope="col" class="num">PV / Akku / Netz</th>
						<th scope="col" class="num">Kosten</th>
					</tr>
				</thead>
				<tbody>
					{#each rows.slice(0, shown) as row (row.id)}
						<tr>
							<td>{row.start}</td>
							<td>{row.end}</td>
							<td>{row.wallbox}</td>
							<td>{row.vehicle}</td>
							<td class="num">{row.energy}</td>
							<td class="num">{row.split}</td>
							<td class="num">{row.cost}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if rows.length > shown}
			<p class="muted more">{shown} von {rows.length} angezeigt</p>
		{/if}
		<div class="actions">
			{#if rows.length > shown}
				<button type="button" onclick={() => (shown += SESSIONS_PAGE)}>Weitere 50 anzeigen</button>
			{/if}
			<button type="button" onclick={download}>CSV herunterladen</button>
		</div>
		{#if rows.length >= SESSIONS_LIMIT}
			<p class="muted limit">Angezeigt werden die {SESSIONS_LIMIT} neuesten Sessions.</p>
		{/if}
	{/if}
</Card>

<style>
	.limit,
	.more {
		margin: 0.5rem 0 0;
		font-size: 0.875rem;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}
</style>
