// Testdaten aus den Vertragsdateien (nur für Tests), jedes Mal frisch durch das Schema geprüft
import * as v from 'valibot';
import limitsJson from '$lib/api/contract/limits.json';
import stateJson from '$lib/api/contract/state.json';
import systemJson from '$lib/api/contract/system.json';
import {
	LimitsSchema,
	ReadingSchema,
	StateResponseSchema,
	SystemResponseSchema,
	type CoreState,
	type Derived,
	type Limits,
	type Reading,
	type SystemResponse
} from '$lib/api/schemas';

export function coreState(): CoreState {
	const { state } = v.parse(StateResponseSchema, stateJson);
	if (state === null) throw new Error('state.json enthält keinen Zustand');
	return state;
}

export function derived(overrides: Partial<Derived> = {}): Derived {
	return { ...coreState().derived, ...overrides };
}

export function reading(
	value: number | string | boolean | null,
	q: 'ok' | 'stale' | 'invalid' = 'ok'
): Reading {
	return v.parse(ReadingSchema, { v: value, ts: '2026-10-09T08:00:00+00:00', q });
}

export function limits(): Limits {
	return v.parse(LimitsSchema, limitsJson);
}

export function system(): SystemResponse {
	return v.parse(SystemResponseSchema, systemJson);
}
