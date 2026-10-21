import { describe, expect, it } from 'vitest';
import error422 from '$lib/api/contract/error-422.json';
import { settingsCurrent } from '$lib/testing/fixtures';
import {
	applyDraft,
	componentId,
	defaultRepriceRange,
	describeWindow,
	draftFrom,
	editorFieldErrors,
	isEnergy,
	newComponent,
	parseDecimal,
	validityText,
	windowsOf,
	type SettingsDraft
} from './model';

const settings = () => settingsCurrent().settings;
const ids = (draft: SettingsDraft) => draft.tariff.components.map((c) => c.id);
const kinds = (draft: SettingsDraft) => draft.tariff.components.map((c) => c.kind);
const component = (draft: SettingsDraft, id: string) => {
	const found = draft.tariff.components.find((c) => c.id === id);
	if (!found) throw new Error(`${id} fehlt`);
	return found;
};
const errorsOf = (draft: SettingsDraft) => {
	const result = applyDraft(settings(), draft);
	if (!('errors' in result)) throw new Error('Fehler erwartet');
	return result.errors;
};

it('liest deutsche Dezimalzahlen', () => {
	expect(parseDecimal('7,3')).toBe(7.3);
	expect(parseDecimal(' 8.11 ')).toBe(8.11);
	expect(parseDecimal('')).toBeNull();
	expect(parseDecimal('abc')).toBe('invalid');
	expect(parseDecimal('1,2,3')).toBe('invalid');
});

it('ändert ohne Eingaben nichts', () => {
	const current = settings();
	expect(applyDraft(current, draftFrom(current))).toEqual({
		settings: current,
		tariffChanged: false
	});
});

it('übernimmt einen Tarifwert mit Komma', () => {
	const current = settings();
	const draft = draftFrom(current);
	component(draft, 'grid_loss').value = '1,23';
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.tariffChanged).toBe(true);
	expect(result.settings.tariff.components.find((c) => c.id === 'grid_loss')?.value_ct).toBe(1.23);
});

it('meldet ungültige Werte und doppelte Monate', () => {
	const current = settings();
	const draft = draftFrom(current);
	component(draft, 'grid_loss').value = 'viel';
	draft.feedIn = [
		{ month: '2026-09', ct: '7,3' },
		{ month: '2026-09', ct: '8' },
		{ month: '2026-10', ct: '-1' }
	];
	expect(applyDraft(current, draft)).toEqual({
		errors: {
			'tariff.components.3.value_ct': 'Zahl, z. B. 8,11',
			'feedIn.1.month': 'Monat doppelt',
			'feedIn.2.ct': 'Wert ab 0'
		}
	});
});

it('beschreibt Zeitfenster und Gültigkeit', () => {
	const gridUsage = settings().tariff.components.find((c) => c.id === 'grid_usage');
	if (!gridUsage || !isEnergy(gridUsage)) throw new Error('grid_usage fehlt');
	expect(describeWindow(gridUsage.windows[0])).toBe('Apr–Sep, Mo–So, 10:00–16:00: × 0,8');
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: null })).toBe('immer');
	expect(validityText({ ...gridUsage, valid_from: '2027-01-01', valid_until: null })).toBe(
		'ab 01.01.2027'
	);
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: '2026-12-31' })).toBe(
		'bis 31.12.2026'
	);
});

it('ordnet Serverfehler dem Feld zu', () => {
	expect(editorFieldErrors(error422.detail, settings())).toEqual({
		'tariff.components.2.value_ct': 'Zahl erwartet'
	});
});

it('schlägt die Neubewertung ab Monatsbeginn vor', () => {
	expect(defaultRepriceRange(new Date('2026-10-09T08:00:00Z'))).toEqual({
		first: '2026-10-01',
		last: '2026-10-09'
	});
});

it('übernimmt eigene Namen der Ladestationen, leere entfallen, zu lange sind ein Fehler', () => {
	const current = settings();
	const draft = draftFrom(current, ['evcs', 'twc']);
	expect(draft.wallboxNames).toEqual({ evcs: '', twc: 'Garage' });
	draft.wallboxNames.evcs = ' Carport ';
	draft.wallboxNames.twc = '';
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.settings.wallbox_names).toEqual({ evcs: 'Carport' });
	expect(result.tariffChanged).toBe(false);
	draft.wallboxNames.evcs = 'x'.repeat(41);
	expect(applyDraft(current, draft)).toEqual({
		errors: { 'wallboxNames.evcs': 'Höchstens 40 Zeichen' }
	});
	expect(
		editorFieldErrors(
			[{ loc: ['body', 'settings', 'wallbox_names', 'evcs'], msg: 'zu lang', type: 'x' }],
			current
		)
	).toEqual({ 'wallboxNames.evcs': 'zu lang' });
});

it('builds ids from names, unique and fixed', () => {
	expect(componentId('Grundgebühr Lieferant', ['spot'])).toBe('grundgebuehr_lieferant');
	expect(componentId('Netz', ['netz'])).toBe('netz_2');
	expect(componentId('Netz', ['netz', 'netz_2'])).toBe('netz_3');
	expect(componentId('Größe ÄÖÜ ß', [])).toBe('groesse_aeoeue_ss');
	expect(componentId('  --  ', [])).toBe('bestandteil');
	expect(componentId('x'.repeat(60), [])).toHaveLength(40);
	expect(componentId('x'.repeat(60), ['x'.repeat(40)])).toBe(`${'x'.repeat(38)}_2`);
});

it('round-trips the default tariff without changes', () => {
	const result = applyDraft(settings(), draftFrom(settings()));
	expect('settings' in result && result.settings).toEqual(settings());
	expect('settings' in result && result.tariffChanged).toBe(false);
});

it('edits a window factor and marks the tariff as changed', () => {
	const draft = draftFrom(settings());
	component(draft, 'grid_usage').windows[0].amount = '0,75';
	const result = applyDraft(settings(), draft);
	expect('settings' in result && result.tariffChanged).toBe(true);
	const saved =
		'settings' in result
			? result.settings.tariff.components.find((c) => c.id === 'grid_usage')
			: null;
	expect(saved && windowsOf(saved)[0].factor).toBe(0.75);
});

it('switches a window to a fixed value', () => {
	const draft = draftFrom(settings());
	const window = component(draft, 'grid_usage').windows[0];
	window.mode = 'value';
	window.amount = '5,5';
	const result = applyDraft(settings(), draft);
	const saved =
		'settings' in result
			? result.settings.tariff.components.find((c) => c.id === 'grid_usage')
			: null;
	expect(saved && windowsOf(saved)[0]).toMatchObject({
		factor: null,
		value_ct: 5.5
	});
});

it('offers the ElWG power price as template when none exists yet', () => {
	const template = newComponent(
		'power_per_kw_year',
		'Neuer Leistungspreis',
		[],
		['energy_per_kwh']
	);
	expect(template).toMatchObject({
		id: 'leistungspreis_elwg',
		name: 'Leistungspreis (ElWG)',
		validFrom: '2027-01-01',
		tierKw: '10',
		minimumKw: '2'
	});
	const another = newComponent(
		'power_per_kw_year',
		'Neuer Leistungspreis',
		[],
		['power_per_kw_year']
	);
	expect(another).toMatchObject({ name: 'Neuer Leistungspreis', validFrom: '' });
	const other = newComponent('fixed_per_month', 'Neue Gebühr', [], []);
	expect(other).toMatchObject({ name: 'Neue Gebühr', validFrom: '' });
});

it('adds a power price with tier and minimum', () => {
	const draft = draftFrom(settings());
	draft.tariff.components.push({
		...newComponent('power_per_kw_year', 'Leistung 2028', ids(draft), kinds(draft)),
		value: '34',
		valueAbove: '68',
		validFrom: '2028-01-01'
	});
	const result = applyDraft(settings(), draft);
	const added = 'settings' in result ? result.settings.tariff.components.at(-1) : null;
	expect(added).toMatchObject({
		kind: 'power_per_kw_year',
		id: 'leistung_2028',
		value_eur: 34,
		value_above_eur: 68,
		tier_kw: 10,
		minimum_kw: 2,
		valid_from: '2028-01-01'
	});
	expect('settings' in result && result.tariffChanged).toBe(false);
});

it('does not mark the tariff as changed for fee and feed-in components', () => {
	const draft = draftFrom(settings());
	component(draft, 'grid_base_fee').value = '4,5';
	draft.tariff.components.push(
		{ ...newComponent('feed_in_per_kwh', 'Abschlag', ids(draft), kinds(draft)), value: '-0,2' },
		{ ...newComponent('fixed_per_month', 'Zähler', ids(draft), kinds(draft)), value: '1' }
	);
	const result = applyDraft(settings(), draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.tariffChanged).toBe(false);
	expect(result.settings.tariff.components.at(-2)).toMatchObject({
		kind: 'feed_in_per_kwh',
		value_ct: -0.2,
		vat: false
	});
});

it('marks the tariff as changed for vat, added and removed energy components', () => {
	const vat = draftFrom(settings());
	vat.tariff.vatPct = '19,5';
	const vatResult = applyDraft(settings(), vat);
	expect('settings' in vatResult && vatResult.tariffChanged).toBe(true);
	expect('settings' in vatResult && vatResult.settings.tariff.vat_pct).toBe(19.5);

	const added = draftFrom(settings());
	added.tariff.components.push({
		...newComponent('energy_per_kwh', 'Zuschlag', ids(added), kinds(added)),
		value: '0,5'
	});
	const addedResult = applyDraft(settings(), added);
	expect('settings' in addedResult && addedResult.tariffChanged).toBe(true);

	const removed = draftFrom(settings());
	removed.tariff.components = removed.tariff.components.filter((c) => c.id !== 'grid_loss');
	const removedResult = applyDraft(settings(), removed);
	expect('settings' in removedResult && removedResult.tariffChanged).toBe(true);
});

it('reports a window that ends before it starts', () => {
	const draft = draftFrom(settings());
	component(draft, 'grid_usage').windows[0].end = '09:00';
	expect(errorsOf(draft)['tariff.components.2.windows.0.end']).toBe(
		'Ende muss nach dem Beginn liegen (00:00 = Tagesende)'
	);
});

it('accepts 00:00 as end of day', () => {
	const draft = draftFrom(settings());
	component(draft, 'grid_usage').windows[0].end = '00:00';
	expect('settings' in applyDraft(settings(), draft)).toBe(true);
});

it('requires a quarter hour', () => {
	const draft = draftFrom(settings());
	component(draft, 'grid_usage').windows[0].start = '10:10';
	expect(errorsOf(draft)['tariff.components.2.windows.0.start']).toBe(
		'Uhrzeit auf einer Viertelstunde, z. B. 10:15'
	);
});

it('requires months, weekdays and an amount in a window', () => {
	const draft = draftFrom(settings());
	const window = component(draft, 'grid_usage').windows[0];
	window.months = [];
	window.weekdays = [];
	window.amount = '';
	expect(errorsOf(draft)).toEqual({
		'tariff.components.2.windows.0.months': 'Mindestens ein Monat',
		'tariff.components.2.windows.0.weekdays': 'Mindestens ein Wochentag',
		'tariff.components.2.windows.0.factor': 'Zahl, z. B. 8,11'
	});
});

it('checks validity order, numbers and names of components', () => {
	const draft = draftFrom(settings());
	const power = component(draft, 'power_price');
	power.validFrom = '2028-01-01';
	power.validUntil = '2027-12-31';
	power.tierKw = '';
	power.minimumKw = '-1';
	power.value = 'x';
	component(draft, 'grid_base_fee').name = ' ';
	draft.tariff.vatPct = '120';
	draft.tariff.fixedPriceCt = '';
	expect(errorsOf(draft)).toMatchObject({
		'tariff.components.6.valid_from': '„gültig ab“ liegt nach „gültig bis“',
		'tariff.components.6.tier_kw': 'Zahl, z. B. 8,11',
		'tariff.components.6.minimum_kw': 'Wert ab 0',
		'tariff.components.6.value_eur': 'Zahl, z. B. 8,11',
		'tariff.components.7.name': 'Name eingeben',
		'tariff.vat_pct': 'Wert von 0 bis 100',
		'tariff.fixed_price_gross_ct': 'Zahl, z. B. 8,11'
	});
});

it('rejects a date that does not exist in the calendar', () => {
	const draft = draftFrom(settings());
	const power = component(draft, 'power_price');
	power.validFrom = '2026-02-30';
	power.validUntil = '2026-13-01';
	expect(errorsOf(draft)).toMatchObject({
		'tariff.components.6.valid_from': 'Datum gibt es nicht',
		'tariff.components.6.valid_until': 'Datum gibt es nicht'
	});
	// Schaltjahr: der 29. Februar 2028 gibt es, der 29. Februar 2027 nicht
	power.validFrom = '2028-02-29';
	power.validUntil = '2027-02-29';
	const errors = errorsOf(draft);
	expect(errors['tariff.components.6.valid_from']).toBeUndefined();
	expect(errors['tariff.components.6.valid_until']).toBe('Datum gibt es nicht');
});

it('maps server errors in windows onto the editor keys', () => {
	// Modell-Prüfungen (pydantic) nennen kein Feld: der Pfad endet beim Zeitfenster
	const detail = [
		{
			type: 'value_error',
			loc: ['body', 'settings', 'tariff', 'components', 2, 'energy_per_kwh', 'windows', 0],
			msg: 'Value error, Ende muss nach dem Beginn liegen (00:00 = Tagesende)'
		}
	];
	expect(editorFieldErrors(detail, settings())).toEqual({
		'tariff.components.2.windows.0': 'Ende muss nach dem Beginn liegen (00:00 = Tagesende)'
	});
});

it('maps server errors of a component and of the tariff onto the editor keys', () => {
	const detail = [
		{
			type: 'value_error',
			loc: ['body', 'settings', 'tariff', 'components', 6, 'power_per_kw_year'],
			msg: 'Value error, valid_from liegt nach valid_until'
		},
		{
			type: 'value_error',
			loc: ['body', 'settings', 'tariff'],
			msg: 'Value error, Leistungspreise dürfen sich zeitlich nicht überschneiden'
		}
	];
	expect(editorFieldErrors(detail, settings())).toEqual({
		'tariff.components.6': 'valid_from liegt nach valid_until',
		tariff: 'Leistungspreise dürfen sich zeitlich nicht überschneiden'
	});
});

it('round-trips the planning settings', () => {
	const current = settings();
	const result = applyDraft(current, draftFrom(current));
	expect('settings' in result && result.settings.planning).toEqual(current.planning);
});

it('shows the planning numbers with a decimal comma', () => {
	const draft = draftFrom(settings());
	expect(draft.planning).toEqual({
		gridChargeAllowed: false,
		marginCt: '2',
		roundtripPct: '88',
		wearCt: '2',
		endValueFactor: '0,9',
		inertiaCt: '0,5'
	});
});

it('takes over edited planning numbers', () => {
	const draft = draftFrom(settings());
	draft.planning = {
		gridChargeAllowed: true,
		marginCt: '2,5',
		roundtripPct: '90',
		wearCt: '1,5',
		endValueFactor: '0,8',
		inertiaCt: '0'
	};
	const result = applyDraft(settings(), draft);
	expect('settings' in result && result.settings.planning).toEqual({
		grid_charge_allowed: true,
		margin_ct: 2.5,
		roundtrip_pct: 90,
		wear_ct: 1.5,
		end_value_factor: 0.8,
		inertia_ct: 0
	});
});

it('checks the planning numbers', () => {
	const draft = draftFrom(settings());
	draft.planning.roundtripPct = '101';
	draft.planning.endValueFactor = '1,5';
	draft.planning.marginCt = '-1';
	draft.planning.wearCt = 'abc';
	draft.planning.inertiaCt = '';
	expect(errorsOf(draft)).toMatchObject({
		'planning.roundtrip_pct': 'Zahl von 50 bis 100',
		'planning.end_value_factor': 'Zahl von 0 bis 1, z. B. 0,9',
		'planning.margin_ct': 'Zahl ab 0, z. B. 2,0',
		'planning.wear_ct': 'Zahl ab 0, z. B. 2,0',
		'planning.inertia_ct': 'Zahl ab 0, z. B. 2,0'
	});
	draft.planning.roundtripPct = '49,9';
	expect(errorsOf(draft)['planning.roundtrip_pct']).toBe('Zahl von 50 bis 100');
});

it('maps server errors of the planning fields to the editor keys', () => {
	const detail = [
		{
			type: 'less_than_equal',
			loc: ['body', 'settings', 'planning', 'roundtrip_pct'],
			msg: 'Input should be less than or equal to 100'
		}
	];
	expect(editorFieldErrors(detail, settings())).toHaveProperty('planning.roundtrip_pct');
});

it('übernimmt Schalter und Sensoren des Lastmodells', () => {
	const current = settings();
	const draft = draftFrom(current);
	expect(draft.learning).toEqual({
		loadModelEnabled: true,
		haSensors: ['sensor.aussentemperatur', 'sensor.wohnzimmer_temperatur'],
		driving: {
			returnReservePct: '20',
			targetMinPct: '50',
			targetMaxPct: '90',
			presenceEntities: {}
		}
	});
	draft.learning.loadModelEnabled = false;
	draft.learning.haSensors = [' person.anna ', 'sensor.aussentemperatur'];
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.settings.learning).toEqual({
		load_model_enabled: false,
		ha_sensors: ['person.anna', 'sensor.aussentemperatur'],
		// der Abschnitt Fahrverhalten bleibt unverändert erhalten
		driving: current.learning.driving
	});
	expect(result.settings.learning.driving).toEqual({
		return_reserve_pct: 20,
		target_min_pct: 50,
		target_max_pct: 90,
		automation: { tesla: true, egolf: false },
		presence_entities: {}
	});
	expect(result.tariffChanged).toBe(false);
});

it('lässt höchstens 8 Sensoren, jeden nur einmal, zu', () => {
	const current = settings();
	const draft = draftFrom(current);
	draft.learning.haSensors = Array.from({ length: 9 }, (_, index) => `sensor.s${index}`);
	expect(applyDraft(current, draft)).toEqual({
		errors: { 'learning.ha_sensors': 'Höchstens 8 Sensoren, jeder nur einmal' }
	});
	draft.learning.haSensors = ['sensor.a', 'sensor.a'];
	expect(applyDraft(current, draft)).toEqual({
		errors: { 'learning.ha_sensors': 'Höchstens 8 Sensoren, jeder nur einmal' }
	});
	draft.learning.haSensors = Array.from({ length: 8 }, (_, index) => `sensor.s${index}`);
	expect('settings' in applyDraft(current, draft)).toBe(true);
});

it('ordnet Serverfehler der Sensorliste zu', () => {
	expect(
		editorFieldErrors(
			[{ loc: ['body', 'settings', 'learning', 'ha_sensors', 1], msg: 'ungültig', type: 'x' }],
			settings()
		)
	).toEqual({ 'learning.ha_sensors.1': 'ungültig' });
});

describe('Fahrverhalten', () => {
	it('übernimmt Puffer, Grenzen und Entitäten ins Formular und legt Felder für alle Autos an', () => {
		const current = settings();
		const draft = draftFrom(current, [], ['tesla', 'egolf']);
		expect(draft.learning.driving).toEqual({
			returnReservePct: '20',
			targetMinPct: '50',
			targetMaxPct: '90',
			presenceEntities: { tesla: '', egolf: '' }
		});
	});

	it('schreibt die Werte zurück, kürzt die Entitäten und lässt leere weg', () => {
		const current = settings();
		const draft = draftFrom(current, [], ['tesla', 'egolf']);
		draft.learning.driving.returnReservePct = '25';
		draft.learning.driving.targetMinPct = '40';
		draft.learning.driving.targetMaxPct = '85';
		draft.learning.driving.presenceEntities.tesla = ' device_tracker.tesla ';
		const result = applyDraft(current, draft);
		if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
		expect(result.settings.learning.driving).toEqual({
			return_reserve_pct: 25,
			target_min_pct: 40,
			target_max_pct: 85,
			// die Automatik wird auf der Fahrzeugkarte geschaltet, nicht hier
			automation: { tesla: true, egolf: false },
			presence_entities: { tesla: 'device_tracker.tesla' }
		});
	});

	it('lehnt Ziel-SOC mindestens über höchstens ab', () => {
		const draft = draftFrom(settings());
		draft.learning.driving.targetMinPct = '80';
		draft.learning.driving.targetMaxPct = '70';
		expect(applyDraft(settings(), draft)).toEqual({
			errors: { 'learning.driving.target_min_pct': 'Ziel-SOC mindestens liegt über höchstens.' }
		});
		draft.learning.driving.targetMaxPct = '80';
		expect('settings' in applyDraft(settings(), draft)).toBe(true);
	});

	it('prüft Zahl und Bereich der drei Felder', () => {
		const draft = draftFrom(settings());
		draft.learning.driving.returnReservePct = '51';
		draft.learning.driving.targetMinPct = '19';
		draft.learning.driving.targetMaxPct = 'viel';
		expect(applyDraft(settings(), draft)).toEqual({
			errors: {
				'learning.driving.return_reserve_pct': 'Ganze Zahl von 0 bis 50',
				'learning.driving.target_min_pct': 'Ganze Zahl von 20 bis 100',
				'learning.driving.target_max_pct': 'Ganze Zahl von 20 bis 100'
			}
		});
		draft.learning.driving.returnReservePct = '20,5';
		draft.learning.driving.targetMinPct = '50';
		draft.learning.driving.targetMaxPct = '90';
		expect(applyDraft(settings(), draft)).toEqual({
			errors: { 'learning.driving.return_reserve_pct': 'Ganze Zahl von 0 bis 50' }
		});
	});

	it('ordnet Serverfehler der Entität dem Feld des Autos zu', () => {
		expect(
			editorFieldErrors(
				[
					{
						loc: ['body', 'settings', 'learning', 'driving', 'presence_entities', 'tesla'],
						msg: 'ungültig',
						type: 'x'
					}
				],
				settings()
			)
		).toEqual({ 'learning.driving.presence_entities.tesla': 'ungültig' });
	});

	it('lädt Einstellungen ohne den Abschnitt mit den Standardwerten', () => {
		const old = structuredClone(settings());
		delete old.learning.driving;
		const draft = draftFrom(old);
		expect(draft.learning.driving).toEqual({
			returnReservePct: '20',
			targetMinPct: '50',
			targetMaxPct: '90',
			presenceEntities: {}
		});
		const result = applyDraft(old, draft);
		if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
		expect(result.settings.learning.driving).toEqual({
			return_reserve_pct: 20,
			target_min_pct: 50,
			target_max_pct: 90,
			automation: {},
			presence_entities: {}
		});
	});
});
