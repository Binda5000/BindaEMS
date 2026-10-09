// Neuer Benutzer: dieselben Regeln wie der Server
import type { NewUser, Role } from '$lib/api/schemas';

export interface UserDraft {
	username: string;
	password: string;
	role: Role;
}

const USERNAME_RE = /^[a-z0-9._-]{3,32}$/;
export const MIN_PASSWORD = 10;
const MAX_PASSWORD = 1024;

export function validateNewUser(
	draft: UserDraft
): { body: NewUser } | { errors: Partial<Record<keyof UserDraft, string>> } {
	const errors: Partial<Record<keyof UserDraft, string>> = {};
	const username = draft.username.trim().toLowerCase();
	if (!USERNAME_RE.test(username)) {
		errors.username = '3–32 Zeichen aus a–z, 0–9, Punkt, Bindestrich, Unterstrich';
	}
	if (draft.password.length < MIN_PASSWORD) errors.password = `Mindestens ${MIN_PASSWORD} Zeichen`;
	else if (draft.password.length > MAX_PASSWORD)
		errors.password = `Höchstens ${MAX_PASSWORD} Zeichen`;
	if (Object.keys(errors).length > 0) return { errors };
	return { body: { username, password: draft.password, role: draft.role } };
}
