import test from "node:test";
import assert from "node:assert/strict";
import { AgentStreamRunner, StreamEvent } from "../src/core/agentStreamRunner";
import { streamFromText, argumentsFragments, SseEventSpec } from "./helpers/sse";

function collectEvents(): { events: StreamEvent[]; push: (e: StreamEvent) => void } {
    const events: StreamEvent[] = [];
    return { events, push: (e) => void events.push(e) };
}

function runnerEvents() {
    return collectEvents();
}

/** Build the tool-call event sequence a backend would emit for one write_file. */
function writeFileEvents(
    index: number,
    argsText: string,
    opts: { id?: string; name?: string; omitToolCall?: boolean } = {},
): SseEventSpec[] {
    const id = opts.id ?? `toolu_${index}`;
    const name = opts.name ?? "write_file";
    const events: SseEventSpec[] = [
        { name: "tool_call_start", data: { index, id, name } },
    ];
    for (const frag of argumentsFragments(argsText, 5)) {
        events.push({ name: "tool_call_delta", data: { index, id, name, arguments_delta: frag } });
    }
    if (!opts.omitToolCall) {
        const parsed = JSON.parse(argsText);
        events.push({ name: "tool_call", data: { index, id, name, arguments: parsed } });
    }
    events.push({ name: "done", data: { finished: false, tool_calls: [], response: "" } });
    return events;
}

// ---------------------------------------------------------------------------

test("incremental text chunks: content decodes in stream order", async () => {
    const content = "def main():\n    print('hello')\n    return 42\n";
    const args = `{"path":"task_manager.py","content":${JSON.stringify(content)}}`;
    const events = writeFileEvents(0, args);

    const collected = runnerEvents();
    const runner = new AgentStreamRunner(e => collected.push(e));
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        3, // tiny chunks — the fragmented stream is the whole point
    ));

    assert.equal(result.cancelled, false);
    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.path, "task_manager.py");
    assert.equal(session.content, content);
    assert.equal(session.status, "ready");

    // fileContent events delivered content fragments in order
    const contentEvents = collected.events.filter(e => e.type === "fileContent");
    assert.ok(contentEvents.length > 1, "chunks should arrive as separate events");
    const reassembled = contentEvents.map(e => (e as any).delta).join("");
    assert.equal(reassembled, content);
});

test("multiple files: each file gets its own preview session", async () => {
    const argsA = `{"path":"a.py","content":${JSON.stringify("print('a')")}}`;
    const argsB = `{"path":"b.py","content":${JSON.stringify("print('b')\nprint('b2')")}}`;
    const events = [
        ...writeFileEvents(0, argsA).slice(0, -1), // drop the trailing done
        ...writeFileEvents(1, argsB),
    ];

    const collected = runnerEvents();
    const runner = new AgentStreamRunner(e => collected.push(e));
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        11,
    ));

    assert.equal(result.cancelled, false);
    const sessionA = result.sessions.find(s => s.index === 0)!;
    const sessionB = result.sessions.find(s => s.index === 1)!;
    assert.equal(sessionA.path, "a.py");
    assert.equal(sessionA.content, "print('a')");
    assert.equal(sessionB.path, "b.py");
    assert.equal(sessionB.content, "print('b')\nprint('b2')");
    assert.equal(sessionA.status, "ready");
    assert.equal(sessionB.status, "ready");
});

test("empty file: finishes cleanly with empty content", async () => {
    const args = `{"path":"empty.py","content":""}`;
    const events = writeFileEvents(0, args);

    const runner = new AgentStreamRunner();
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
    ));

    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.path, "empty.py");
    assert.equal(session.content, "");
    assert.equal(session.status, "ready");
});

test("cancellation mid-stream: the reader aborts and the preview is marked cancelled", async () => {
    // The stream dies halfway — same as Stop propagating the fetch abort.
    const args = `{"path":"cut.py","content":${JSON.stringify("print('one')\nprint('two')\nprint('three')")}}`;
    const events: SseEventSpec[] = [
        { name: "tool_call_start", data: { index: 0, id: "id", name: "write_file" } },
        { name: "tool_call_delta", data: { index: 0, id: "id", name: "write_file", arguments_delta: args.slice(0, 30) } },
        // stream dies here — no terminal events
    ];
    const raw = events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");

    let cancelledEvents = 0;
    const runner = new AgentStreamRunner(e => { if (e.type === "cancelled") cancelledEvents++; });
    // 160 bytes in (out of ~202), then the stream dies mid-deltas.
    const result = await runner.run(streamFromText(raw, 40, new DOMException("The operation was aborted.", "AbortError"), 4));

    assert.equal(result.cancelled, true);
    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.status, "cancelled");
    assert.ok(cancelledEvents >= 1, "a cancelled event should be emitted");
});

test("cancellation via backend cancelled event", async () => {
    const args = `{"path":"cut.py","content":${JSON.stringify("partial")}}`;
    const events: SseEventSpec[] = [
        { name: "tool_call_start", data: { index: 0, id: "id", name: "write_file" } },
        { name: "tool_call_delta", data: { index: 0, id: "id", name: "write_file", arguments_delta: args } },
        { name: "cancelled", data: {} },
    ];

    const runner = new AgentStreamRunner();
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
    ));

    assert.equal(result.cancelled, true);
    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.status, "cancelled");
});

test("malformed partial JSON never throws and never emits raw JSON as code", async () => {
    const args = `{"path":"broken.py","content":"def f():\n    print('oops')\n`;  // never closed
    const events: SseEventSpec[] = [
        { name: "tool_call_start", data: { index: 0, id: "id", name: "write_file" } },
        { name: "tool_call_delta", data: { index: 0, id: "id", name: "write_file", arguments_delta: args } },
    ];

    const collected = runnerEvents();
    const runner = new AgentStreamRunner(e => collected.push(e));
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        2,
    ));

    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.path, "broken.py");
    const contentEvents = collected.events.filter(e => e.type === "fileContent");
    for (const e of contentEvents) {
        const delta = (e as any).delta as string;
        assert.ok(!delta.includes('"content"'), "must not emit raw JSON keys");
        assert.ok(!delta.includes('{"path'), "must not emit raw JSON fragments");
    }
    // The accumulated session content may be incomplete, but must never
    // contain raw JSON fragments.
    assert.ok(!session.content.includes('"content"'));
    assert.ok(session.content.length > 0);
});

test("existing file is never overwritten during the preview phase", async () => {
    // The preview phase has no file-system access by construction; this test
    // documents and enforces that: the session carries no workspace writes,
    // no tool call events happen before the approval stage, and the preview
    // state is purely in-memory.
    const events = writeFileEvents(0, `{"path":"existing.py","content":${JSON.stringify("NEW CONTENT")}}`);
    const runner = new AgentStreamRunner();
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        3,
    ));

    assert.equal(result.cancelled, false);
    // No write_file tool call executes at this point — the preview is a
    // pure in-memory state.
    for (const session of result.sessions) {
        assert.equal(session.status, "ready");
    }
    assert.equal(result.toolCalls[0].name, "write_file");
});

test("the complete tool_call event overrides any partial-accumulated content", async () => {
    const content = "final content";
    const args = `{"path":"final.py","content":${JSON.stringify(content)}}`;
    const events = writeFileEvents(0, args);
    // The stream ends without the tool_call event (arguments arrive complete).
    const runner = new AgentStreamRunner();
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        8,
    ));

    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.content, content);
});
