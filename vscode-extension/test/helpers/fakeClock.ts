/**
 * Deterministic clock for testing paced typing without real waits.
 *
 * `advance(ms)` fires every timer that is due, in timestamp order, and gives
 * the async reveal loop a chance to run (and schedule its next tick) after
 * each one — so a single `advance(1000)` simulates one second of typing
 * exactly, at whatever speed the pacer was configured with.
 */

import { TimerHandle, TypewriterClock } from "../../src/core/typewriter";

interface ScheduledTimer {
    at: number;
    fn: () => void;
}

export class FakeClock implements TypewriterClock {
    private timers = new Map<number, ScheduledTimer>();
    private nextId = 1;
    now = 0;

    setTimeout(fn: () => void, ms: number): TimerHandle {
        const id = this.nextId++;
        this.timers.set(id, { at: this.now + Math.max(0, ms), fn });
        return id;
    }

    clearTimeout(handle: TimerHandle): void {
        this.timers.delete(handle as number);
    }

    /** Number of timers still pending (should drop to 0 after cancel). */
    get pendingTimers(): number {
        return this.timers.size;
    }

    /** Fire all timers due within `ms`, letting async reveals settle. */
    async advance(ms: number): Promise<void> {
        // Let any reveal started by a synchronous push() settle and schedule
        // its follow-up timer *before* time moves forward.
        await flushAsync();
        const target = this.now + ms;
        for (;;) {
            let dueId: number | undefined;
            let dueAt = Infinity;
            for (const [id, timer] of this.timers) {
                if (timer.at <= target && timer.at < dueAt) {
                    dueId = id;
                    dueAt = timer.at;
                }
            }
            if (dueId === undefined) break;

            const timer = this.timers.get(dueId)!;
            this.timers.delete(dueId);
            this.now = timer.at;
            timer.fn();
            await flushAsync();
        }
        this.now = target;
    }
}

/** Let pending microtasks/immediates (the async reveal loop) settle. */
export async function flushAsync(rounds = 3): Promise<void> {
    for (let i = 0; i < rounds; i++) {
        await new Promise<void>((resolve) => setImmediate(resolve));
    }
}
