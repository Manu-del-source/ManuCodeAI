import test from "node:test";
import assert from "node:assert/strict";
import { PartialArgumentsParser } from "../src/core/partialArgumentsParser";

function collect(parser: PartialArgumentsParser, args: string, fragmentSize = 7): { content: string; path: string } {
    let content = "";
    let path = "";
    for (let i = 0; i < args.length; i += fragmentSize) {
        const out = parser.feed(args.slice(i, i + fragmentSize));
        content += out.contentDelta;
        path += out.pathDelta;
    }
    const flush = parser.flush();
    content += flush.contentDelta;
    path += flush.pathDelta;
    return { content, path };
}

test("decodes a simple two-key arguments stream with tiny fragments", () => {
    const args = `{"path":"task_manager.py","content":"def main():\\n    print('hello')\\n"}`;
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, args, 1);
    assert.equal(path, "task_manager.py");
    assert.equal(content, "def main():\n    print('hello')\n");
});

test("decodes content when the path arrives AFTER the content (any key order)", () => {
    const args = `{"content":"print('hi')","path":"late_path.py"}`;
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, args, 3);
    assert.equal(path, "late_path.py");
    assert.equal(content, "print('hi')");
});

test("decodes escape sequences including \\uXXXX and \\\\ and \\\"", () => {
    const args = `{"path":"x.py","content":"a\\nb\\t\\r\\u0041\\u00e9\\\\\\"end"}`;
    const parser = new PartialArgumentsParser();
    const { content } = collect(parser, args, 5);
    assert.equal(content, "a\nb\t\rA\xe9\\\"end");
});

test("holds an incomplete escape at chunk end, then decodes it", () => {
    // Escape arrives split across the fragment boundary.
    const args = `{"path":"x.py","content":"ok\\nend"}`;
    const parser = new PartialArgumentsParser();
    const out1 = parser.feed(`{"path":"x.py","content":"ok\\`);
    const out2 = parser.feed(`nend"}`);
    const flush = parser.flush();
    const content = out1.contentDelta + out2.contentDelta + flush.contentDelta;
    assert.equal(content, "ok\nend");
});

test("holds an incomplete \\uXXXX escape across fragments", () => {
    const args = `{"path":"x.py","content":"A\\u0042B"}`; // A + B + B
    const parser = new PartialArgumentsParser();
    const out1 = parser.feed(`{"path":"x.py","content":"A\\u00`);
    const out2 = parser.feed(`41B"}`);
    const flush = parser.flush();
    const content = out1.contentDelta + out2.contentDelta + flush.contentDelta;
    assert.equal(content, "AAB");
});

test("raw newlines inside strings are tolerated and passed through", () => {
    const args = `{"path":"x.py","content":"line1\nline2\nline3"}`;
    const parser = new PartialArgumentsParser();
    const { content } = collect(parser, args, 7);
    assert.equal(content, "line1\nline2\nline3");
});

test("malformed: truncated stream never throws and never emits raw JSON", () => {
    const args = `{"path":"task_manager.py","content":"def main():\n    print('hello')\n    # TODO: finish`;
    const parser = new PartialArgumentsParser();
    let content = "";
    let path = "";
    for (let i = 0; i < args.length; i += 5) {
        const out = parser.feed(args.slice(i, i + 5));
        content += out.contentDelta;
        path += out.pathDelta;
    }
    const flush = parser.flush();
    content += flush.contentDelta;
    path += flush.pathDelta;

    assert.equal(path, "task_manager.py");
    assert.ok(content.startsWith("def main():"));
    assert.ok(!content.includes("\"content\""), "must not leak raw JSON fragments");
    assert.ok(!content.includes("{") && !content.includes("}"), "must not leak raw JSON fragments");
});

test("malformed: content containing a quoted path-like string does not corrupt path extraction", () => {
    // The content itself contains the text "path":"evil.py" — the parser must
    // not treat it as the path key.
    const args = `{"path":"real.py","content":"the \\"path\\":\\"evil.py\\" inside content"}`;
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, args, 4);
    assert.equal(path, "real.py");
    assert.equal(content, 'the "path":"evil.py" inside content');
});

test("malformed: multiple keys, unknown keys with nested objects are skipped", () => {
    const args = `{"path":"real.py","description":{"nested":"ignored{["},"content":"code","extra":123}`;
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, args, 3);
    assert.equal(path, "real.py");
    assert.equal(content, "code");
});

test("malformed: root object already closed — trailing garbage is ignored", () => {
    const args = `{"path":"real.py","content":"code"} TRAILING {"garbage": "nope"}`;
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, args, 3);
    assert.equal(path, "real.py");
    assert.equal(content, "code");
});

test("empty arguments", () => {
    const parser = new PartialArgumentsParser();
    const { content, path } = collect(parser, "{}", 1);
    assert.equal(path, "");
    assert.equal(content, "");
});

test("unicode content survives in small fragments", () => {
    const args = `{"path":"x.py","content":"# héllo wörld — τ est"}`;
    const parser = new PartialArgumentsParser();
    const { content } = collect(parser, args, 1);
    assert.equal(content, "# héllo wörld — τ est");
});

test("flush emits an incomplete escape remainder without throwing", () => {
    const parser = new PartialArgumentsParser();
    parser.feed(`{"path":"x.py","content":"abc\\`);
    const flush = parser.flush();
    assert.equal(flush.contentDelta, "\\");
});
