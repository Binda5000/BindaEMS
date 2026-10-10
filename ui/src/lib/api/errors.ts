// Fehler der API als deutsche Texte für die Anzeige

export const NETWORK_ERROR = 'Keine Verbindung zum Server';
export const CONTRACT_ERROR = 'Unerwartete Antwort vom Server';
const UNKNOWN_ERROR = 'Unbekannter Fehler';

export class ApiError extends Error {
	/** HTTP-Status; 0 = keine Verbindung */
	readonly status: number;
	readonly detail: string;
	readonly body: unknown;
	readonly retryAfterS: number | null;

	constructor(
		status: number,
		detail: string,
		body: unknown = null,
		retryAfterS: number | null = null
	) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
		this.body = body;
		this.retryAfterS = retryAfterS;
	}
}

type Loc = readonly (string | number)[];

interface Issue {
	type: string;
	loc: Loc;
	msg: string;
}

// Meldungen von Pydantic nach Fehlerart
const MESSAGES: Record<string, string> = {
	float_parsing: 'Zahl erwartet',
	float_type: 'Zahl erwartet',
	int_parsing: 'Zahl erwartet',
	int_type: 'Zahl erwartet',
	missing: 'Angabe fehlt',
	string_too_short: 'zu kurz',
	string_too_long: 'zu lang',
	greater_than: 'Wert zu klein',
	greater_than_equal: 'Wert zu klein',
	less_than: 'Wert zu groß',
	less_than_equal: 'Wert zu groß',
	literal_error: 'Wert nicht erlaubt',
	enum: 'Wert nicht erlaubt',
	extra_forbidden: 'unbekanntes Feld',
	json_invalid: 'Ungültiges JSON'
};

function isIssue(value: unknown): value is Issue {
	if (typeof value !== 'object' || value === null) return false;
	const { type, loc, msg } = value as Record<string, unknown>;
	return (
		typeof type === 'string' &&
		typeof msg === 'string' &&
		Array.isArray(loc) &&
		loc.every((part) => typeof part === 'string' || typeof part === 'number')
	);
}

function issues(detail: unknown): Issue[] {
	return Array.isArray(detail) ? detail.filter(isIssue) : [];
}

function message(issue: Issue): string {
	if (issue.type === 'value_error') return issue.msg.replace(/^Value error, /, '');
	return MESSAGES[issue.type] ?? issue.msg;
}

function startsWith(loc: Loc, prefix: Loc): boolean {
	return prefix.length <= loc.length && prefix.every((part, index) => loc[index] === part);
}

/** Text zu `detail` einer Fehlerantwort: Text unverändert, Fehlerlisten Zeile für Zeile. */
export function detailText(detail: unknown): string {
	if (typeof detail === 'string' && detail) return detail;
	const list = issues(detail);
	if (list.length === 0) return UNKNOWN_ERROR;
	return list
		.map((issue) => {
			const path = (startsWith(issue.loc, ['body']) ? issue.loc.slice(1) : issue.loc).join('.');
			return path ? `${path}: ${message(issue)}` : message(issue);
		})
		.join('\n');
}

/**
 * Text zu einer Fehlerantwort: `detail` der API, sonst nach Status. Ohne JSON (Fehlerseite des
 * Proxys, etwa während die app neu startet) nennt der Text wenigstens den Status.
 */
export function responseText(status: number, detail: unknown): string {
	const text = detailText(detail);
	if (text !== UNKNOWN_ERROR) return text;
	if (status === 502 || status === 503 || status === 504) {
		return `Server nicht erreichbar (HTTP ${status})`;
	}
	return `${UNKNOWN_ERROR} (HTTP ${status})`;
}

/** Fehler je Feld unterhalb von `prefix`; Schlüssel ist der restliche Pfad mit Punkten. */
export function fieldErrors(
	detail: unknown,
	prefix: readonly (string | number)[] = ['body']
): Record<string, string> {
	const result: Record<string, string> = {};
	for (const issue of issues(detail)) {
		if (!startsWith(issue.loc, prefix)) continue;
		const key = issue.loc.slice(prefix.length).join('.');
		result[key] ??= message(issue);
	}
	return result;
}
