import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { AgentStreamRunner } from "../src/core/agentStreamRunner";
import { streamFromText, argumentsFragments, SseEventSpec } from "./helpers/sse";
import { readWorkspaceFile } from "../src/core/fileOps";

/**
 * End-to-end preview-phase integration test: drive the runner (the exact
 * code the extension uses) over a real workspace file and assert the file
 * is byte-identical after every stage of generation — proving the preview
 * never writes to the workspace as long as the approval path is not entered.
 */
test("the preview phase does not write workspace files (runner + real fs)", async (t) => {
    const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "manucodeai-preview-"));
    t.after(async () => { await fs.rm(workspace, { recursive: true, force: true }); });

    const target = path.join(workspace, "task_manager.py");
    const original = "# task manager\nprint('original')\n";
    await fs.writeFile(target, original, "utf8");

    const content = "# task manager\n\nclass TaskManager:\n    def __init__(self):\n        self.tasks = []\n";
    const args = `{"path":"task_manager.py","content":${JSON.stringify(content)}}`;
    const events: SseEventSpec[] = [
        { name: "file_change", data: { current_file: "task_manager.py" } },
        { name: "tool_call_start", data: { index: 0, id: "toolu_1", name: "write_file" } },
    ];
    for (const frag of argumentsFragments(args, 5)) {
        events.push({ name: "tool_call_delta", data: { index: 0, id: "toolu_1", name: "write_file", arguments_delta: frag } });
    }
    events.push({ name: "tool_call", data: { index: 0, id: "toolu_1", name: "write_file", arguments: JSON.parse(args) } });
    events.push({ name: "done", data: { finished: false, tool_calls: [], response: "" } });

    const runner = new AgentStreamRunner();
    const result = await runner.run(streamFromText(
        events.map(e => `event: ${e.name}\ndata: ${JSON.stringify(e.data)}\n\n`).join(""),
        3,
    ));

    // 1. The preview session exists and carries the same content.
    const session = result.sessions.find(s => s.index === 0)!;
    assert.equal(session.path, "task_manager.py");
    assert.equal(session.content, content);
    assert.equal(session.status, "ready");

    // 2. The workspace file is byte-identical — nothing was written.
    const onDisk = await readWorkspaceFile(target);
    assert.equal(onDisk.content, original);
});
