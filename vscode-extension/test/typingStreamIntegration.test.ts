import test from "node:test";
import assert from "node:assert/strict";
import { AgentStreamRunner } from "../src/core/agentStreamRunner";
import { TYPING_SPEED_CPS, Typewriter, TypingSpeed, typingTiming } from "../src/core/typewriter";
import { FakeClock } from "./helpers/fakeClock";
import { argumentsFragments, streamFromText, SseEventSpec } from "./helpers/sse";

/**
 * End-to-end pacing tests: the real AgentStreamRunner feeds real decoded
 * deltas into the same per-file Typewriter the extension uses, and an
 * in-memory "editor" records exactly what would have been inserted.
 */

interface TestFile {
    path: string;
    content: string;
}

interface Harness {
    runner: AgentStreamRunner;
    clock: FakeClock;
    editors: Map<number, { text: string; typer: Typewriter }>;
    fileDone: number[];
    run: () => Promise<Awaited<ReturnType<AgentStreamRunner["run"]>>>;
}

function makeStream(files: TestFile[], fragmentSize = 5): ReadableStream<Uint8Array> {
    const events: SseEventSpec[] = [];
    files.forEach((file, index) => {
        const args = `{"path":${JSON.stringify(file.path)},"content":${JSON.stringify(file.content)}}`;
        const id = `toolu_${index}`;
        events.push({ name: "tool_call_start", data: { index, id, name: "write_file" } });
        for (const frag of argumentsFragments(args, fragmentSize)) {
            events.push({ name: "tool_call_delta", data: { index, id, name: "write_file", arguments_delta: frag } });
        }
        events.push({ name: "tool_call", data: { index, id, name: "write_file", arguments: JSON.parse(args) } });
    });
    events.push({ name: "done", data: { finished: false, tool_calls: [], response: "" } });

    const raw = events
        .map((e) => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");
    return streamFromText(raw, 13);
}

function harness(files: TestFile[], speed: number): Harness {
    const clock = new FakeClock();
    const editors = new Map<number, { text: string; typer: Typewriter }>();
    const fileDone: number[] = [];

    const editorFor = (index: number) => {
        let editor = editors.get(index);
        if (!editor) {
            editor = {
                text: "",
                typer: new Typewriter({
                    charsPerSecond: speed,
                    clock,
                    onType: (text) => {
                        editor!.text += text;
                    },
                }),
            };
            editors.set(index, editor);
        }
        return editor;
    };

    const runner = new AgentStreamRunner((event) => {
        switch (event.type) {
            case "fileStart":
                editorFor(event.session.index);
                break;
            case "fileContent":
                // Exactly like PreviewManager.appendPreview(): queue and return.
                editorFor(event.session.index).typer.push(event.delta);
                break;
            case "fileDone":
                fileDone.push(event.session.index);
                break;
            default:
                break;
        }
    });

    return {
        runner,
        clock,
        editors,
        fileDone,
        run: () => runner.run(makeStream(files)),
    };
}

const sample = (seed: string, lines: number): string => {
    const rows = Array.from({ length: lines }, (_, i) => `    value_${i} = ${JSON.stringify(`${seed}-${i}`)}`);
    return `${seed} = {\n${rows.join("\n")}\n}\n`;
};

const FILES: TestFile[] = [
    { path: "alpha.py", content: sample("alpha", 30) },
    { path: "beta.py", content: sample("beta", 30) },
];

// ---------------------------------------------------------------------------

test("paced editor rendering never back-pressures the stream: generation completes while typing is still queued", async () => {
    const h = harness(FILES, TYPING_SPEED_CPS.slow);

    const result = await h.run();

    // Generation finished at full speed — both files fully decoded, ready.
    assert.equal(result.cancelled, false);
    assert.equal(result.sessions.length, 2);
    for (const session of result.sessions) {
        assert.equal(session.status, "ready");
        assert.equal(session.content, FILES[session.index].content);
    }
    assert.deepEqual(h.fileDone.sort(), [0, 1], "multi-file generation still works");

    // The editor has only revealed its immediate first chunk per file: the
    // stream was never slowed down to match the typing pace.
    for (const [index, editor] of h.editors) {
        const content = FILES[index].content;
        assert.ok(editor.text.length > 0, "first characters are shown immediately");
        assert.ok(
            editor.text.length < content.length / 5,
            `only ${editor.text.length} of ${content.length} chars revealed without waiting for typing`,
        );
        assert.ok(editor.typer.pending > 0, "most of the file is still queued for slow typing");
        assert.equal(editor.typer.pending + editor.text.length, content.length);
        assert.ok(content.startsWith(editor.text), "what is shown is an exact prefix");
    }

    // The extension's "finish typing" step drains both previews; the result
    // is byte-exact and in order (this is what the diff review then shows).
    await h.clock.advance(60_000);
    for (const [index, editor] of h.editors) {
        assert.equal(editor.text, FILES[index].content);
        assert.equal(editor.typer.pending, 0);
    }
});

test("all three speed settings pace the same stream at their configured rates", async () => {
    const revealed: Record<string, number[]> = {};

    for (const speed of ["slow", "normal", "fast"] as TypingSpeed[]) {
        const h = harness(FILES, TYPING_SPEED_CPS[speed]);
        await h.run();
        await h.clock.advance(1000);
        revealed[speed] = [...h.editors.values()].map((editor) => editor.text.length);
    }

    for (const speed of ["slow", "normal", "fast"] as TypingSpeed[]) {
        const cps = TYPING_SPEED_CPS[speed];
        for (const count of revealed[speed]) {
            assert.ok(
                count >= cps * 0.9 && count <= cps * 1.1 + typingTiming(cps).chunkSize,
                `${speed}: ${count} chars in 1s, expected ~${cps}`,
            );
        }
    }

    assert.ok(revealed.slow[0] < revealed.normal[0], "slow types slower than normal");
    assert.ok(revealed.normal[0] < revealed.fast[0], "normal types slower than fast");
});

test("Stop during typing: reveals stop immediately, pending updates are cancelled, no text is corrupted", async () => {
    const h = harness(FILES, TYPING_SPEED_CPS.normal);
    await h.run();

    await h.clock.advance(200);
    const snapshot = new Map([...h.editors].map(([index, editor]) => [index, editor.text]));

    // Stop button: cancel every preview's typewriter.
    for (const editor of h.editors.values()) editor.typer.cancel();

    await h.clock.advance(10_000);

    for (const [index, editor] of h.editors) {
        const before = snapshot.get(index)!;
        assert.ok(before.length > 0, "typing had started");
        assert.equal(editor.text, before, "no editor update after Stop");
        assert.equal(editor.typer.pending, 0, "queued characters dropped");
        assert.equal(editor.typer.cancelled, true);
        assert.ok(FILES[index].content.startsWith(before), "kept text is an exact prefix");
    }
    assert.equal(h.clock.pendingTimers, 0, "no reveal timers survive Stop");
});

test("late deltas arriving during typing are appended after everything already queued", async () => {
    // Simulates a slow network: the queue grows in bursts while typing runs.
    const clock = new FakeClock();
    let typed = "";
    const typer = new Typewriter({
        charsPerSecond: TYPING_SPEED_CPS.slow,
        clock,
        onType: (text) => {
            typed += text;
        },
    });

    const content = sample("gamma", 20);
    for (let i = 0; i < content.length; i += 40) {
        typer.push(content.slice(i, i + 40));
        await clock.advance(25);
    }
    await clock.advance(120_000);

    assert.equal(typed, content);
    assert.equal(typer.pending, 0);
});
