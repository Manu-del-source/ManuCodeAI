import test from "node:test";
import assert from "node:assert/strict";
import {
    DEFAULT_CHARS_PER_SECOND,
    DEFAULT_TYPING_SPEED,
    TYPING_SPEED_CPS,
    Typewriter,
    TypingSpeed,
    resolveCharsPerSecond,
    resolveTypingSpeed,
    typingTiming,
} from "../src/core/typewriter";
import { FakeClock, flushAsync } from "./helpers/fakeClock";

const SPEEDS: TypingSpeed[] = ["slow", "normal", "fast"];

interface Recorder {
    typer: Typewriter;
    clock: FakeClock;
    chunks: Array<{ at: number; text: string }>;
    text: () => string;
}

function recorder(options: { speed?: number; clock?: FakeClock } = {}): Recorder {
    const clock = options.clock ?? new FakeClock();
    const chunks: Array<{ at: number; text: string }> = [];
    const typer = new Typewriter({
        charsPerSecond: options.speed ?? DEFAULT_CHARS_PER_SECOND,
        clock,
        onType: (text) => {
            chunks.push({ at: clock.now, text });
        },
    });
    return {
        typer,
        clock,
        chunks,
        text: () => chunks.map((chunk) => chunk.text).join(""),
    };
}

function sampleText(length: number): string {
    const base = "class TaskManager:\n    def add(self, task):\n        self.tasks.append(task)\n";
    let text = "";
    while (text.length < length) text += base;
    return text.slice(0, length);
}

// ---------------------------------------------------------------------------
// Speed configuration
// ---------------------------------------------------------------------------

test("speed presets are slow=15, normal=35, fast=70 characters per second", () => {
    assert.deepEqual(TYPING_SPEED_CPS, { slow: 15, normal: 35, fast: 70 });
    for (const speed of SPEEDS) {
        assert.equal(resolveCharsPerSecond(speed), TYPING_SPEED_CPS[speed]);
    }
});

test("the default typing speed is human-like (30-50 cps)", () => {
    assert.equal(DEFAULT_TYPING_SPEED, "normal");
    assert.equal(DEFAULT_CHARS_PER_SECOND, 35);
    assert.ok(DEFAULT_CHARS_PER_SECOND >= 30 && DEFAULT_CHARS_PER_SECOND <= 50);
});

test("resolveTypingSpeed normalizes settings values and falls back to normal", () => {
    assert.equal(resolveTypingSpeed("slow"), "slow");
    assert.equal(resolveTypingSpeed(" FAST "), "fast");
    assert.equal(resolveTypingSpeed("Normal"), "normal");
    assert.equal(resolveTypingSpeed("turbo"), DEFAULT_TYPING_SPEED);
    assert.equal(resolveTypingSpeed(undefined), DEFAULT_TYPING_SPEED);
    assert.equal(resolveTypingSpeed(42), DEFAULT_TYPING_SPEED);
});

test("resolveCharsPerSecond accepts names, custom numbers and numeric strings", () => {
    assert.equal(resolveCharsPerSecond("slow"), 15);
    assert.equal(resolveCharsPerSecond("normal"), 35);
    assert.equal(resolveCharsPerSecond("fast"), 70);
    assert.equal(resolveCharsPerSecond(42), 42);
    assert.equal(resolveCharsPerSecond("42"), 42);
    assert.equal(resolveCharsPerSecond(" 50 "), 50);
    // Invalid / out-of-range values fall back to the default.
    assert.equal(resolveCharsPerSecond("nonsense"), DEFAULT_CHARS_PER_SECOND);
    assert.equal(resolveCharsPerSecond(undefined), DEFAULT_CHARS_PER_SECOND);
    assert.equal(resolveCharsPerSecond(null), DEFAULT_CHARS_PER_SECOND);
    assert.equal(resolveCharsPerSecond(0), DEFAULT_CHARS_PER_SECOND);
    assert.equal(resolveCharsPerSecond(-20), DEFAULT_CHARS_PER_SECOND);
    assert.equal(resolveCharsPerSecond(100_000), 1000);
});

test("typingTiming keeps chunks tiny and yields exactly the requested rate", () => {
    for (const speed of SPEEDS) {
        const cps = TYPING_SPEED_CPS[speed];
        const timing = typingTiming(cps);
        assert.ok(timing.chunkSize >= 1 && timing.chunkSize <= 4, "chunks stay tiny");
        assert.ok(timing.intervalMs > 0, "there is a delay between chunks");
        const effective = (timing.chunkSize / timing.intervalMs) * 1000;
        assert.ok(Math.abs(effective - cps) < 0.01 * cps, `${speed}: ${effective} cps ≈ ${cps}`);
    }
});

// ---------------------------------------------------------------------------
// Pacing per speed setting
// ---------------------------------------------------------------------------

for (const speed of SPEEDS) {
    test(`${speed} (${TYPING_SPEED_CPS[speed]} cps): reveals text at the configured rate, in order, without loss`, async () => {
        const cps = TYPING_SPEED_CPS[speed];
        const input = sampleText(2000);
        const rec = recorder({ speed: cps });

        rec.typer.push(input);
        await rec.clock.advance(1000);

        const typed = rec.text();
        const revealed = typed.length;

        // ~one second of typing at the requested rate (first chunk is free).
        assert.ok(
            revealed >= cps * 0.9 && revealed <= cps * 1.1 + typingTiming(cps).chunkSize,
            `${speed}: revealed ${revealed} chars in 1s, expected ~${cps}`,
        );

        // Ordering, no duplicates, no gaps: everything revealed is an exact
        // prefix of the queued text, built from the chunk sequence.
        assert.equal(typed, input.slice(0, revealed));
        assert.equal(rec.typer.text, typed);

        // Chunks stay very small.
        for (const chunk of rec.chunks) {
            assert.ok(chunk.text.length <= typingTiming(cps).chunkSize);
        }

        // Not everything has been typed yet — the queue is still paced.
        assert.ok(rec.typer.pending > 0);
        assert.equal(rec.typer.pending + revealed, input.length);
    });
}

test("slower settings reveal less text in the same amount of time", async () => {
    const input = sampleText(1000);
    const slow = recorder({ speed: TYPING_SPEED_CPS.slow });
    const normal = recorder({ speed: TYPING_SPEED_CPS.normal });
    const fast = recorder({ speed: TYPING_SPEED_CPS.fast });

    for (const rec of [slow, normal, fast]) rec.typer.push(input);

    await slow.clock.advance(500);
    await normal.clock.advance(500);
    await fast.clock.advance(500);

    assert.ok(slow.text().length < normal.text().length, "slow < normal");
    assert.ok(normal.text().length < fast.text().length, "normal < fast");
});

test("the first characters appear immediately — no initial delay", () => {
    for (const speed of SPEEDS) {
        const rec = recorder({ speed: TYPING_SPEED_CPS[speed] });
        rec.typer.push("def main():\n");
        // No clock advance at all — the first chunk must already be visible.
        assert.ok(rec.text().length > 0, `${speed}: first chunk shown without waiting`);
        assert.equal(rec.chunks[0].at, 0);
    }
});

test("text that arrives in bursts keeps strict ordering and never duplicates", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.slow });
    const parts = ["def f():", "\n    return", " 42", "\n"];

    for (const part of parts) {
        rec.typer.push(part);
        await rec.clock.advance(10);
    }
    await rec.clock.advance(5000);

    assert.equal(rec.text(), parts.join(""));
    assert.equal(rec.typer.pending, 0);
});

// ---------------------------------------------------------------------------
// Draining and cancellation
// ---------------------------------------------------------------------------

test("drain() resolves only after every queued character has been revealed", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.normal });
    const input = sampleText(200);
    rec.typer.push(input);

    let drained = false;
    const drainedPromise = rec.typer.drain().then(() => {
        drained = true;
    });

    await rec.clock.advance(10);
    assert.equal(drained, false, "not drained while characters are still queued");

    await rec.clock.advance(10_000);
    await drainedPromise;

    assert.equal(drained, true);
    assert.equal(rec.text(), input);
    assert.equal(rec.typer.pending, 0);
    assert.equal(rec.typer.idle, true);
});

test("cancel() during typing stops reveals immediately and cancels pending updates", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.slow });
    const input = sampleText(1000);
    rec.typer.push(input);

    await rec.clock.advance(300);
    const revealedAtCancel = rec.text();
    assert.ok(revealedAtCancel.length > 0 && revealedAtCancel.length < input.length);

    rec.typer.cancel();

    assert.equal(rec.typer.cancelled, true);
    assert.equal(rec.typer.pending, 0, "queued characters are dropped");
    assert.equal(rec.clock.pendingTimers, 0, "pending reveal timers are cleared");

    // Nothing may be written to the editor after Stop.
    await rec.clock.advance(5000);
    assert.equal(rec.text(), revealedAtCancel, "no editor update after cancel");
    assert.ok(input.startsWith(rec.text()), "the kept text is an exact prefix");

    // The typewriter is dead: late chunks can never appear.
    rec.typer.push("MORE TEXT");
    await rec.clock.advance(5000);
    assert.equal(rec.text(), revealedAtCancel);
});

test("cancel() releases a pending drain() immediately (no hang)", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.slow });
    rec.typer.push(sampleText(500));

    let drained = false;
    const pending = rec.typer.drain().then(() => {
        drained = true;
    });

    await rec.clock.advance(100);
    rec.typer.cancel();
    await pending;
    assert.equal(drained, true);
});

test("cancel() while a reveal is in flight prevents any further reveals", async () => {
    let release: (() => void) | undefined;
    const chunks: string[] = [];
    const clock = new FakeClock();
    const typer = new Typewriter({
        charsPerSecond: TYPING_SPEED_CPS.slow,
        clock,
        onType: (text) => {
            chunks.push(text);
            if (chunks.length === 1) {
                return new Promise<void>((resolve) => {
                    release = resolve;
                });
            }
            return undefined;
        },
    });

    typer.push("abcdefghij");
    // The first reveal is in flight and blocked in editor.edit().
    assert.equal(chunks.join(""), "a");

    typer.cancel();
    release?.();
    await flushAsync();
    await clock.advance(5000);

    assert.equal(chunks.join(""), "a", "only the in-flight chunk was written");
    assert.equal(typer.pending, 0);
    assert.equal(clock.pendingTimers, 0);
});

test("astral characters are never split across reveals", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.slow });
    const input = "const icons = ['🙂', '🎉', '🚀']; // ünïcödé\n".repeat(4);
    rec.typer.push(input);

    await rec.clock.advance(30_000);

    assert.equal(rec.text(), input);
    for (const chunk of rec.chunks) {
        const first = chunk.text.charCodeAt(0);
        const last = chunk.text.charCodeAt(chunk.text.length - 1);
        assert.ok(!(first >= 0xdc00 && first <= 0xdfff), "chunk starts with a lone low surrogate");
        assert.ok(!(last >= 0xd800 && last <= 0xdbff), "chunk ends with a lone high surrogate");
    }
});

test("setSpeed() re-paces characters that were queued before the change", async () => {
    const rec = recorder({ speed: TYPING_SPEED_CPS.slow });
    const input = sampleText(1000);
    rec.typer.push(input);

    await rec.clock.advance(200);
    const before = rec.text().length;

    rec.typer.setSpeed(TYPING_SPEED_CPS.fast);
    assert.equal(rec.typer.speed, TYPING_SPEED_CPS.fast);

    await rec.clock.advance(1000);
    const after = rec.text().length - before;
    assert.ok(after > TYPING_SPEED_CPS.fast * 0.9, `fast backlog catch-up: ${after} chars/s`);
    assert.equal(rec.text(), input.slice(0, rec.text().length));
});
