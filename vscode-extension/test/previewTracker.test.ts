import test from "node:test";
import assert from "node:assert/strict";
import { PreviewTracker, normalizePath } from "../src/core/previewTracker";

test("starts a session and tracks path/content accumulation", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "toolu_1", "write_file");
    tracker.appendPath(0, "task_manager");
    tracker.appendPath(0, ".py");
    tracker.appendContent(0, "print(");
    tracker.appendContent(0, "'hello')");

    const session = tracker.get(0)!;
    assert.equal(session.path, "task_manager.py");
    assert.equal(session.content, "print('hello')");
    assert.equal(session.status, "streaming");
    assert.equal(session.baselineCaptured, false);
});

test("separate sessions per tool index, same target path keeps separate state", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id_a", "write_file");
    tracker.start(1, "id_b", "write_file");
    tracker.appendPath(0, "src/a.py");
    tracker.appendPath(1, "src/b.py");
    tracker.appendContent(0, "aaa");
    tracker.appendContent(1, "bbbb");

    assert.equal(tracker.get(0)!.path, "src/a.py");
    assert.equal(tracker.get(1)!.path, "src/b.py");
    assert.equal(tracker.get(0)!.content, "aaa");
    assert.equal(tracker.get(1)!.content, "bbbb");
    assert.equal(tracker.list().length, 2);
});

test("normalizePath strips leading ./ and normalizes separators", () => {
    assert.equal(normalizePath("./foo/bar.py"), "foo/bar.py");
    assert.equal(normalizePath("foo\\bar\\baz.py"), "foo/bar/baz.py");
    assert.equal(normalizePath("  spaced.py  "), "spaced.py");
});

test("finish overrides path/content with the authoritative values and marks ready", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id", "write_file");
    tracker.appendContent(0, "partial");
    const session = tracker.finish(0, "src/real.py", "complete content");
    assert.equal(session!.status, "ready");
    assert.equal(session!.path, "src/real.py");
    assert.equal(session!.content, "complete content");
});

test("cancelAll marks every streaming/ready session cancelled", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id", "write_file");
    tracker.appendContent(0, "x");
    tracker.start(1, "id2", "write_file");
    tracker.appendContent(1, "y");
    tracker.finish(1, "a.py", "y");

    const cancelled = tracker.cancelAll();
    assert.equal(cancelled.length, 2);
    assert.equal(tracker.get(0)!.status, "cancelled");
    assert.equal(tracker.get(1)!.status, "cancelled");
});

test("baseline capture sets baselineCaptured and the baseline state", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id", "write_file");
    tracker.setBaseline(0, "original content", true);
    const session = tracker.get(0)!;
    assert.equal(session.baselineCaptured, true);
    assert.equal(session.baselineExists, true);
    assert.equal(session.baselineContent, "original content");
});

test("markApplied/markRejected only apply to non-cancelled sessions", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id", "write_file");
    tracker.cancel(0);
    tracker.markApplied(0);
    assert.equal(tracker.get(0)!.status, "cancelled"); // not applied — user cancelled

    tracker.start(1, "id2", "write_file");
    tracker.finish(1, "a.py", "");
    tracker.markApplied(1);
    assert.equal(tracker.get(1)!.status, "applied");
});

test("empty session with no content finishes cleanly", () => {
    const tracker = new PreviewTracker();
    tracker.start(0, "id", "write_file");
    const session = tracker.finish(0, "empty.py", "");
    assert.equal(session!.status, "ready");
    assert.equal(session!.content, "");
    assert.equal(session!.path, "empty.py");
});
