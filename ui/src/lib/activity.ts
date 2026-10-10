// Bedienung im Browser (Tippen, Klicken, Antippen) verlängert die Sitzung (Spec 14: Ablauf nach
// Leerlauf): höchstens einmal je Minute eine Anfrage im Vordergrund, nur bei sichtbarem Tab.
// Abfragen im Takt laufen im Hintergrund und verlängern nicht – ohne das hier liefe ein Admin,
// der lange in einem Formular tippt, in den Ablauf und verlöre seine Eingaben.

const EVENTS = ['keydown', 'pointerdown'] as const;

export interface ActivityOptions {
	/** höchstens eine Anfrage je Abstand (ms) */
	intervalMs?: number;
	now?: () => number;
	hidden?: () => boolean;
}

/** Meldet Bedienung auf `target` über `ping`; gibt `stop` zurück. */
export function watchActivity(
	target: EventTarget,
	ping: () => Promise<unknown>,
	{
		intervalMs = 60_000,
		now = Date.now,
		hidden = () => document.visibilityState === 'hidden'
	}: ActivityOptions = {}
): () => void {
	let last = now(); // die Seite hat eben im Vordergrund geladen
	const onActivity = (): void => {
		if (hidden()) return;
		const time = now();
		if (time - last < intervalMs) return;
		last = time;
		ping().catch(() => {
			// 401 führt der API-Client zur Anmeldung, Netzfehler zählen nicht
		});
	};
	for (const type of EVENTS) target.addEventListener(type, onActivity, { capture: true });
	return () => {
		for (const type of EVENTS) target.removeEventListener(type, onActivity, { capture: true });
	};
}
