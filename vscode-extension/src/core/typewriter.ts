/**
 * Human-like typing pace for the progressive editor stream.
 *
 * The backend SSE stream is always consumed at full network speed by
 * `AgentStreamRunner` — it is never slowed down or back-pressured. This
 * module only controls how fast characters that have *already arrived* are
 * revealed in the preview editor:
 *
 *  - text is queued and revealed character-by-character (or in very small
 *    chunks at high speeds), one reveal at a time, in exact order;
 *  - the first character is released immediately — there is no initial
 *    delay before generated code appears;
 *  - every character is released exactly once (no duplicates, no gaps);
 *  - `cancel()` drops the queue, clears the pending timer and resolves any
 *    pending `drain()` immediately (the Stop button).
 *
 * The module is pure (no VS Code import) and takes an injectable clock so
 * the pacing can be unit-tested deterministically.
 */

export type TypingSpeed = "slow" | "normal" | "fast";

/** Characters per second for each configurable speed. */
export const TYPING_SPEED_CPS: Readonly<Record<TypingSpeed, number>> = {
    slow: 15,
    normal: 35,
    fast: 70,
};

/**
 * Default speed. 35 characters per second sits in the human typing range
 * (30–50 cps) — fast enough to stay responsive, slow enough to read.
 */
export const DEFAULT_TYPING_SPEED: TypingSpeed = "normal";

export const DEFAULT_CHARS_PER_SECOND: number = TYPING_SPEED_CPS[DEFAULT_TYPING_SPEED];

/** Hard bounds for a custom characters-per-second value. */
export const MIN_CHARS_PER_SECOND = 1;
export const MAX_CHARS_PER_SECOND = 1000;

/**
 * Target spacing between two reveals. The chunk size is derived from it, so
 * fast speeds reveal a couple of characters at a time instead of hammering
 * the editor with sub-frame edits — still "very small chunks".
 */
const TARGET_TICK_MS = 25;

/** Largest number of characters released in a single reveal. */
const MAX_CHUNK_SIZE = 4;

export function isTypingSpeed(value: unknown): value is TypingSpeed {
    return value === "slow" || value === "normal" || value === "fast";
}

/**
 * Normalize a settings/vs-code value into one of the three speed names.
 * Unknown values fall back to the default (normal).
 */
export function resolveTypingSpeed(value: unknown): TypingSpeed {
    if (typeof value === "string") {
        const normalized = value.trim().toLowerCase();
        if (isTypingSpeed(normalized)) return normalized;
    }
    return DEFAULT_TYPING_SPEED;
}

/**
 * Resolve a raw setting/config value into characters per second.
 *
 * Accepts a speed name ("slow" | "normal" | "fast", case-insensitive), a
 * positive number (custom cps), or a numeric string. Anything else — and
 * anything out of range — resolves to the default 35 cps.
 */
export function resolveCharsPerSecond(value: unknown): number {
    if (typeof value === "number" && Number.isFinite(value)) {
        return clampCharsPerSecond(value);
    }
    if (typeof value === "string") {
        const normalized = value.trim().toLowerCase();
        if (isTypingSpeed(normalized)) return TYPING_SPEED_CPS[normalized];
        const numeric = Number.parseFloat(normalized);
        if (Number.isFinite(numeric)) return clampCharsPerSecond(numeric);
    }
    return DEFAULT_CHARS_PER_SECOND;
}

function clampCharsPerSecond(value: number): number {
    if (!Number.isFinite(value) || value <= 0) return DEFAULT_CHARS_PER_SECOND;
    return Math.min(MAX_CHARS_PER_SECOND, Math.max(MIN_CHARS_PER_SECOND, value));
}

export interface TypingTiming {
    /** Characters released per reveal. */
    chunkSize: number;
    /** Delay between two reveals, in milliseconds. */
    intervalMs: number;
}

/**
 * Derive the reveal timing for a characters-per-second rate.
 *
 * The pair (chunkSize, intervalMs) always yields the requested rate —
 * e.g. 35 cps → 1 character every ~28.6 ms; 70 cps → 2 characters every
 * ~28.6 ms. Chunks stay tiny so the reveal still reads as typing.
 */
export function typingTiming(charsPerSecond: number): TypingTiming {
    const cps = clampCharsPerSecond(charsPerSecond);
    const idealChunk = (cps * TARGET_TICK_MS) / 1000;
    const chunkSize = Math.min(MAX_CHUNK_SIZE, Math.max(1, Math.round(idealChunk)));
    const intervalMs = (chunkSize / cps) * 1000;
    return { chunkSize, intervalMs };
}

// ---------------------------------------------------------------------------
// Typewriter
// ---------------------------------------------------------------------------

export type TimerHandle = unknown;

/** Minimal scheduler surface, injectable so tests can control time. */
export interface TypewriterClock {
    setTimeout(callback: () => void, ms: number): TimerHandle;
    clearTimeout(handle: TimerHandle): void;
}

const realClock: TypewriterClock = {
    setTimeout: (callback, ms) => setTimeout(callback, ms),
    clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface TypewriterOptions {
    /** Reveal rate. Defaults to {@link DEFAULT_CHARS_PER_SECOND}. */
    charsPerSecond?: number;
    /** Receives each revealed chunk, in order. Awaited before the next one. */
    onType: (text: string) => void | Promise<void>;
    /** Injectable scheduler (tests only). */
    clock?: TypewriterClock;
}

/**
 * Paced reveal queue for one generated file.
 *
 * `push()` is synchronous and never blocks the caller, so the SSE reader
 * keeps draining at network speed while the editor types at the configured
 * rate. Reveals never overlap: each `onType()` call is awaited before the
 * next timer is scheduled, which guarantees document ordering.
 */
export class Typewriter {
    private readonly clock: TypewriterClock;
    private readonly onType: (text: string) => void | Promise<void>;

    private charsPerSecond: number;
    private chunkSize: number;
    private intervalMs: number;

    /** Received but not yet revealed characters, in arrival order. */
    private queue = "";
    /** Characters already handed to `onType`, in order. */
    private revealed = "";
    private timer: TimerHandle | undefined;
    private revealing = false;
    private stopped = false;
    private waiters: Array<() => void> = [];

    constructor(options: TypewriterOptions) {
        this.clock = options.clock ?? realClock;
        this.onType = options.onType;
        this.charsPerSecond = clampCharsPerSecond(options.charsPerSecond ?? DEFAULT_CHARS_PER_SECOND);
        const timing = typingTiming(this.charsPerSecond);
        this.chunkSize = timing.chunkSize;
        this.intervalMs = timing.intervalMs;
    }

    /** Characters per second currently in effect. */
    get speed(): number {
        return this.charsPerSecond;
    }

    /** Characters handed to `onType` so far (exact reveal order). */
    get text(): string {
        return this.revealed;
    }

    /** Characters still waiting to be revealed. */
    get pending(): number {
        return this.queue.length;
    }

    /** True once `cancel()` was called; a stopped typewriter accepts no text. */
    get cancelled(): boolean {
        return this.stopped;
    }

    /** True while there is nothing left to reveal. */
    get idle(): boolean {
        return this.queue.length === 0 && !this.revealing && this.timer === undefined;
    }

    /** Change the reveal rate for text that has not been revealed yet. */
    setSpeed(charsPerSecond: number): void {
        this.charsPerSecond = clampCharsPerSecond(charsPerSecond);
        const timing = typingTiming(this.charsPerSecond);
        this.chunkSize = timing.chunkSize;
        this.intervalMs = timing.intervalMs;
    }

    /**
     * Queue newly-arrived text. Returns immediately — it never waits for the
     * typing pace, so stream consumption and generation stay unaffected.
     */
    push(text: string): void {
        if (this.stopped || !text) return;
        this.queue += text;
        if (!this.revealing && this.timer === undefined) {
            // Reveal the first chunk immediately: no delay before the first
            // generated code becomes visible.
            void this.revealNext();
        }
    }

    /**
     * Resolves once every queued character has been revealed, or immediately
     * after `cancel()`. Safe to call repeatedly.
     */
    drain(): Promise<void> {
        if (this.stopped || this.idle) return Promise.resolve();
        return new Promise<void>((resolve) => {
            this.waiters.push(resolve);
        });
    }

    /**
     * Stop immediately: drop unrevealed text, clear the pending timer and
     * release anyone awaiting `drain()`. Already-revealed text is kept.
     */
    cancel(): void {
        if (this.stopped) return;
        this.stopped = true;
        this.queue = "";
        if (this.timer !== undefined) {
            this.clock.clearTimeout(this.timer);
            this.timer = undefined;
        }
        // Release every waiter, even if a reveal is still in flight — Stop
        // must never leave `drain()` hanging.
        this.releaseAllWaiters();
    }

    private async revealNext(): Promise<void> {
        if (this.stopped || this.revealing) return;
        if (this.queue.length === 0) {
            this.releaseWaiters();
            return;
        }

        this.revealing = true;
        let keepGoing = false;
        try {
            const chunk = this.takeChunk();

            // Cancellation cannot interleave between this check and the
            // synchronous start of onType(), so no text is ever released
            // after Stop.
            if (this.stopped) {
                this.queue = "";
                return;
            }

            this.revealed += chunk;
            try {
                await this.onType(chunk);
            } catch {
                // The preview editor may have been closed mid-typing; the
                // remaining text is still revealed so ordering is intact.
            }
            keepGoing = !this.stopped && this.queue.length > 0;
        } finally {
            this.revealing = false;
            if (this.stopped) {
                // cancel() already released the waiters.
                this.releaseAllWaiters();
                return;
            }
            if (keepGoing) {
                this.scheduleNext();
            } else {
                this.releaseWaiters();
            }
        }
    }

    /**
     * Remove the next reveal from the queue. Chunks never split a UTF-16
     * surrogate pair, so emoji/astral characters cannot be rendered as
     * broken half-characters while typing.
     */
    private takeChunk(): string {
        if (this.queue.length === 0) return "";
        let size = Math.min(this.chunkSize, this.queue.length);
        const lastUnit = this.queue.charCodeAt(size - 1);
        if (size < this.queue.length && lastUnit >= 0xd800 && lastUnit <= 0xdbff) {
            size = Math.min(size + 1, this.queue.length);
        }
        const chunk = this.queue.slice(0, size);
        this.queue = this.queue.slice(size);
        return chunk;
    }

    private scheduleNext(): void {
        if (this.stopped || this.timer !== undefined) return;
        this.timer = this.clock.setTimeout(() => {
            this.timer = undefined;
            void this.revealNext();
        }, this.intervalMs);
    }

    private releaseWaiters(): void {
        if (this.stopped) {
            this.releaseAllWaiters();
            return;
        }
        if (this.queue.length > 0 || this.revealing) return;
        this.releaseAllWaiters();
    }

    private releaseAllWaiters(): void {
        const waiters = this.waiters;
        this.waiters = [];
        for (const resolve of waiters) resolve();
    }
}
