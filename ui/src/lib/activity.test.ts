import { expect, it, vi } from 'vitest';
import { watchActivity } from './activity';

function setup(hidden = false) {
	let time = 0;
	const ping = vi.fn().mockResolvedValue(undefined);
	const target = new EventTarget();
	const stop = watchActivity(target, ping, { now: () => time, hidden: () => hidden });
	return { ping, target, stop, at: (ms: number) => (time = ms) };
}

it('verlängert die Sitzung bei Bedienung höchstens einmal je Minute', () => {
	const { ping, target, stop, at } = setup();
	at(30_000);
	target.dispatchEvent(new Event('keydown'));
	expect(ping).not.toHaveBeenCalled(); // die Seite hat eben erst geladen
	at(61_000);
	target.dispatchEvent(new Event('keydown'));
	target.dispatchEvent(new Event('pointerdown'));
	expect(ping).toHaveBeenCalledTimes(1);
	at(122_000);
	target.dispatchEvent(new Event('pointerdown'));
	expect(ping).toHaveBeenCalledTimes(2);
	stop();
	at(300_000);
	target.dispatchEvent(new Event('keydown'));
	expect(ping).toHaveBeenCalledTimes(2);
});

it('zählt in verborgenen Tabs nicht als Bedienung', () => {
	const { ping, target, at } = setup(true);
	at(120_000);
	target.dispatchEvent(new Event('keydown'));
	expect(ping).not.toHaveBeenCalled();
});
