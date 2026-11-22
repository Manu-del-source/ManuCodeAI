import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs/promises";
import * as os from "os";
import * as path from "path";
import { readWorkspaceFile, writeWorkspaceFile, detectExternalModification } from "../src/core/fileOps";
import { decideApprovalOutcome, mapApprovalChoice, mapConflictChoice } from "../src/core/approval";

async function makeTmpWorkspace(t: { after: (fn: () => Promise<void>) => void }): Promise<string> {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "manucodeai-approval-"));
    t.after(async () => { await fs.rm(dir, { recursive: true, force: true }); });
    return dir;
}

test("existing-file isolation: nothing is written before approval, and reject leaves the file byte-identical", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "task_manager.py");
    const original = "# task manager\nprint('original')\n";
    await fs.writeFile(target, original, "utf8");

    // The preview phase is pure in-memory state — simulate the stream by
    // only reading the baseline (what the extension does at generation start).
    const baseline = await readWorkspaceFile(target);
    assert.equal(baseline.exists, true);
    assert.equal(baseline.content, original);

    // Simulate an external edit mid-stream.
    await fs.writeFile(target, original + "# user edit\n", "utf8");

    // Approval decision for a "reject" outcome — file must remain untouched.
    const current = await readWorkspaceFile(target);
    const outcome = decideApprovalOutcome({
        baselineExists: baseline.exists,
        baselineContent: baseline.content,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "reject",
        conflictChoice: null,
    });
    assert.equal(outcome.decision, "reject");

    // The file on disk must still hold the external edit — untouched.
    const after = await fs.readFile(target, "utf8");
    assert.equal(after, original + "# user edit\n");
});

test("approval with no external modification writes the proposed content", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "task_manager.py");
    const original = "# original\n";
    await fs.writeFile(target, original, "utf8");

    const baseline = await readWorkspaceFile(target);
    // Simulate the extension's full approval flow.
    const current = await readWorkspaceFile(target);
    const conflict = detectExternalModification(baseline, current, "task_manager.py");
    assert.equal(conflict, null);

    const outcome = decideApprovalOutcome({
        baselineExists: baseline.exists,
        baselineContent: baseline.content,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "apply",
        conflictChoice: null,
    });
    assert.equal(outcome.decision, "apply");

    await writeWorkspaceFile(target, "# proposed\n");
    const after = await fs.readFile(target, "utf8");
    assert.equal(after, "# proposed\n");
});

test("approval with external modification requires an explicit confirm, else cancels", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "task_manager.py");
    const original = "# original\n";
    await fs.writeFile(target, original, "utf8");

    const baseline = await readWorkspaceFile(target);
    await fs.writeFile(target, original + "# user changed it\n", "utf8");

    const current = await readWorkspaceFile(target);
    const conflict = detectExternalModification(baseline, current, "task_manager.py");
    assert.ok(conflict && conflict.includes("modified"));

    // User cancels the conflict prompt.
    const cancel = decideApprovalOutcome({
        baselineExists: baseline.exists,
        baselineContent: baseline.content,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "apply",
        conflictChoice: "cancel",
    });
    assert.equal(cancel.decision, "cancel");

    // User confirms overwrite.
    const overwrite = decideApprovalOutcome({
        baselineExists: baseline.exists,
        baselineContent: baseline.content,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "apply",
        conflictChoice: "apply",
    });
    assert.equal(overwrite.decision, "apply");
});

test("new file: external creation between baseline and approval is detected", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "fresh.py");

    const baseline = await readWorkspaceFile(target);
    assert.equal(baseline.exists, false);

    // External creation mid-flight.
    await fs.writeFile(target, "# external\n", "utf8");

    const current = await readWorkspaceFile(target);
    const conflict = detectExternalModification(baseline, current, "fresh.py");
    assert.ok(conflict && conflict.includes("created externally"));

    const cancel = decideApprovalOutcome({
        baselineExists: false,
        baselineContent: null,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "apply",
        conflictChoice: "cancel",
    });
    assert.equal(cancel.decision, "cancel");

    const overwrite = decideApprovalOutcome({
        baselineExists: false,
        baselineContent: null,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "apply",
        conflictChoice: "apply",
    });
    assert.equal(overwrite.decision, "apply");
});

test("cancel-on-dismiss leaves the file untouched", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "task_manager.py");
    await fs.writeFile(target, "# original\n", "utf8");

    const outcome = decideApprovalOutcome({
        baselineExists: true,
        baselineContent: "# original\n",
        currentExists: true,
        currentContent: "# original\n",
        approvalChoice: "cancel",
        conflictChoice: null,
    });
    assert.equal(outcome.decision, "cancel");
    const after = await fs.readFile(target, "utf8");
    assert.equal(after, "# original\n");
});

test("mapApprovalChoice and mapConflictChoice filter dialog answers", () => {
    assert.equal(mapApprovalChoice("Apply"), "apply");
    assert.equal(mapApprovalChoice("Apply changes"), "apply");
    assert.equal(mapApprovalChoice("Apply anyway"), "apply");
    assert.equal(mapApprovalChoice("Reject"), "reject");
    assert.equal(mapApprovalChoice(undefined), "cancel");
    assert.equal(mapApprovalChoice("Something else"), "cancel");

    assert.equal(mapConflictChoice("Apply anyway"), "apply");
    assert.equal(mapConflictChoice("Cancel"), "cancel");
    assert.equal(mapConflictChoice(undefined), "cancel");
});

test("rejection path: proposed content is never written when rejected", async (t) => {
    const workspace = await makeTmpWorkspace(t);
    const target = path.join(workspace, "task_manager.py");
    const original = "# original\n";
    await fs.writeFile(target, original, "utf8");

    // Simulate the reject flow: baseline + current captured, decision is reject.
    const baseline = await readWorkspaceFile(target);
    const current = await readWorkspaceFile(target);
    const outcome = decideApprovalOutcome({
        baselineExists: baseline.exists,
        baselineContent: baseline.content,
        currentExists: current.exists,
        currentContent: current.content,
        approvalChoice: "reject",
        conflictChoice: null,
    });
    assert.equal(outcome.decision, "reject");

    // The flow must NOT call writeWorkspaceFile here.
    const after = await fs.readFile(target, "utf8");
    assert.equal(after, original);
});
