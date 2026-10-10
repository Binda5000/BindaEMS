<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import type { SettingsCurrent, SettingsMeta } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { DASH, formatCt, formatDateTime, formatNumber, NBSP } from '$lib/format';
	import { Resource } from '$lib/resource.svelte';
	import { hasRole } from '$lib/roles';
	import ImportExport from '$lib/settings/ImportExport.svelte';
	import LimitsView from '$lib/settings/LimitsView.svelte';
	import { defaultRepriceRange } from '$lib/settings/model';
	import RepriceForm from '$lib/settings/RepriceForm.svelte';
	import SettingsEditor from '$lib/settings/SettingsEditor.svelte';
	import TariffView from '$lib/settings/TariffView.svelte';

	const admin = $derived(hasRole(page.data.user, 'admin'));

	const settings = new Resource((o) => api.settings(o));
	const limits = new Resource((o) => api.limits(o));
	let versions = $state.raw<Resource<SettingsMeta[]> | null>(null);

	onMount(() => {
		const stops = [settings.start(), limits.start()];
		if (admin) {
			const resource = new Resource((o) => api.settingsVersions(o));
			versions = resource;
			stops.push(resource.start());
		}
		return () => stops.forEach((stop) => stop());
	});

	let editing = $state(false);
	let editorKey = $state(0); // neu aufbauen nach „Neu laden“
	let saved = $state<string | null>(null);
	let showReprice = $state(false);

	const VAT_MODES = { auto: 'automatisch erkennen', net: 'netto', gross: 'brutto' } as const;
	const FALLBACKS = { net: 'netto', gross: 'brutto' } as const;
	const REFERENCES = { energy_charts: 'Energy-Charts', awattar: 'aWATTar' } as const;
	const SOURCES = {
		ui: 'UI',
		ha: 'Home Assistant',
		cli: 'Kommandozeile',
		system: 'System'
	} as const;

	function startEditing() {
		saved = null;
		editorKey += 1;
		editing = true;
	}

	async function afterSave(result: SettingsCurrent, tariffChanged: boolean) {
		editing = false;
		saved = `Version ${result.version} gespeichert.`;
		showReprice = showReprice || tariffChanged;
		await Promise.all([settings.refresh(), versions?.refresh()]);
	}

	async function reload() {
		await settings.refresh();
		editorKey += 1;
	}

	async function afterImport(result: SettingsCurrent) {
		saved = `Version ${result.version} übernommen.`;
		showReprice = true;
		await Promise.all([settings.refresh(), versions?.refresh()]);
	}

	function feedInRows(current: SettingsCurrent): [string, number][] {
		return Object.entries(current.settings.feed_in.monthly_ct).sort(([a], [b]) =>
			a < b ? -1 : a > b ? 1 : 0
		);
	}
</script>

<div class="settings">
	{#if settings.error}
		<Notice level="error">{settings.error.detail}</Notice>
	{/if}
	{#if saved}
		<Notice level="info">{saved}</Notice>
	{/if}

	{#if settings.data}
		{@const current = settings.data}
		{#if current.warnings.length > 0}
			<ul class="plain">
				{#each current.warnings as warning, index (index)}
					<li><Notice level="warning">{warning}</Notice></li>
				{/each}
			</ul>
		{/if}

		<p class="version muted">
			Version {current.version} vom {formatDateTime(current.created_at)} ({current.actor}){#if current.comment}:
				„{current.comment}“{/if}
		</p>

		{#if admin}
			{#if editing}
				<Card title="Einstellungen bearbeiten">
					{#key editorKey}
						<SettingsEditor
							{current}
							wallboxes={limits.data?.wallboxes}
							save={api.saveSettings}
							onSaved={afterSave}
							onReload={reload}
							onCancel={() => (editing = false)}
						/>
					{/key}
				</Card>
			{:else}
				<p><button type="button" class="primary" onclick={startEditing}>Bearbeiten</button></p>
			{/if}
			{#if showReprice}
				<Card title="Abrechnung neu bewerten">
					<RepriceForm initial={defaultRepriceRange(new Date())} reprice={api.reprice} />
				</Card>
			{/if}
		{/if}

		<Card title="Tarif">
			<TariffView settings={current.settings} />
		</Card>

		<Card title="Einspeisung (OeMAG)">
			{#if feedInRows(current).length > 0}
				<table class="compact">
					<thead>
						<tr><th scope="col">Monat</th><th scope="col" class="num">Marktpreis</th></tr>
					</thead>
					<tbody>
						{#each feedInRows(current) as [month, ct] (month)}
							<tr><td>{month}</td><td class="num">{formatCt(ct)}</td></tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="muted">Noch kein Monatswert eingetragen.</p>
			{/if}
		</Card>

		<Card title="Preise">
			<dl class="facts">
				<dt>Börsenpreise kommen</dt>
				<dd>{VAT_MODES[current.settings.prices.vat_mode]}</dd>
				<dt>Annahme, wenn nicht erkennbar</dt>
				<dd>{FALLBACKS[current.settings.prices.vat_fallback]}</dd>
				<dt>Referenzquelle</dt>
				<dd>{REFERENCES[current.settings.prices.reference_source]}</dd>
			</dl>
		</Card>

		<Card title="PV-Modell">
			<dl class="facts">
				<dt>Performance Ratio</dt>
				<dd>{formatNumber(current.settings.pv_model.performance_ratio, 2)}</dd>
				<dt>Temperaturkoeffizient</dt>
				<dd>{formatNumber(current.settings.pv_model.temp_coeff_pct_per_k, 2)}{NBSP}%/K</dd>
				<dt>NOCT</dt>
				<dd>{formatNumber(current.settings.pv_model.noct_c, 0)}{NBSP}°C</dd>
			</dl>
		</Card>
	{:else if !settings.error}
		<p class="muted">Lädt …</p>
	{/if}

	<Card title="Harte Grenzen">
		{#if limits.data}
			<LimitsView limits={limits.data} />
		{:else if limits.error}
			<Notice level="error">{limits.error.detail}</Notice>
		{/if}
	</Card>

	{#if admin}
		<Card title="Versionen">
			{#if versions?.data}
				<div class="scroll">
					<table>
						<thead>
							<tr>
								<th scope="col" class="num">Version</th>
								<th scope="col">Zeit</th>
								<th scope="col">Wer</th>
								<th scope="col">Quelle</th>
								<th scope="col">Kommentar</th>
							</tr>
						</thead>
						<tbody>
							{#each versions.data as version (version.version)}
								<tr>
									<td class="num">{version.version}</td>
									<td>{formatDateTime(version.created_at)}</td>
									<td>{version.actor}</td>
									<td>{SOURCES[version.source]}</td>
									<td>{version.comment ?? DASH}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else if versions?.error}
				<Notice level="error">{versions.error.detail}</Notice>
			{/if}
		</Card>

		<Card title="Export und Import">
			<ImportExport importSettings={api.importSettings} onImported={afterImport} />
		</Card>
	{/if}
</div>

<style>
	.settings {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.plain {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}

	.version {
		margin: 0;
	}

	p {
		margin: 0;
	}

	.compact {
		width: auto;
	}

	.scroll {
		overflow-x: auto;
	}

	table:not(.compact) {
		min-width: max-content;
	}
</style>
