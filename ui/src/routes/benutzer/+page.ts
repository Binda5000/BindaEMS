// Nur für Admins: alle anderen sehen „Keine Berechtigung“
import { requireAdmin } from '$lib/roles';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ parent }) => {
	requireAdmin((await parent()).user);
	return {};
};
