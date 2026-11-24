import { before, test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

// These source-contract tests protect the user's autonomous workflow from
// regressions back to per-file approval dialogs or silent unsafe overwrites.
let extensionSource = "";
before(async () => {
    extensionSource = await readFile(resolve(process.cwd(), "src/extension.ts"), "utf8");
});

test("generated files are saved without approval dialogs", () => {
    assert.match(extensionSource, /private async autoSavePreview\(/);
    assert.match(extensionSource, /return await this\.autoSavePreview\(/);
    assert.doesNotMatch(extensionSource, /showWarningMessage|showInformationMessage|showQuickPick/);
    assert.doesNotMatch(extensionSource, /from "\.\/core\/approval"/);
});

test("baseline is captured only after the complete file path arrives", () => {
    const filePathBlock = extensionSource.match(/case "filePath": \{([\s\S]*?)\n            \}/)?.[1] ?? "";
    const fileDoneBlock = extensionSource.match(/case "fileDone": \{([\s\S]*?)\n            \}/)?.[1] ?? "";
    assert.doesNotMatch(filePathBlock, /captureBaseline/);
    assert.match(fileDoneBlock, /captureBaseline\(event\.session\)/);
});

test("automatic saves refuse external conflicts and verify disk contents", () => {
    assert.match(extensionSource, /detectExternalModification\(baseline, current, relPath\)/);
    assert.match(extensionSource, /const saved = await readWorkspaceFile\(target\)/);
    assert.match(extensionSource, /saved\.content !== proposed/);
});

test("ordinary commands run automatically but destructive command patterns are blocked", () => {
    assert.match(extensionSource, /Running automatically:/);
    assert.match(extensionSource, /Blocked potentially destructive command/);
    assert.doesNotMatch(extensionSource, /Execute this command in the workspace/);
});
