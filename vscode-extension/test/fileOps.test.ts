import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
    detectExternalModification,
    readWorkspaceFile,
    writeWorkspaceFile,
} from "../src/core/fileOps";

test("automatic-save conflict guard accepts an unchanged existing file", () => {
    assert.equal(
        detectExternalModification(
            { exists: true, content: "original" },
            { exists: true, content: "original" },
            "src/app.ts",
        ),
        null,
    );
});

test("automatic-save conflict guard rejects modified, deleted and newly-created targets", () => {
    assert.match(
        detectExternalModification(
            { exists: true, content: "original" },
            { exists: true, content: "changed elsewhere" },
            "src/app.ts",
        ) ?? "",
        /modified externally/,
    );
    assert.match(
        detectExternalModification(
            { exists: true, content: "original" },
            { exists: false, content: "" },
            "src/app.ts",
        ) ?? "",
        /deleted externally/,
    );
    assert.match(
        detectExternalModification(
            { exists: false, content: "" },
            { exists: true, content: "created elsewhere" },
            "src/new.ts",
        ) ?? "",
        /created externally/,
    );
});

test("workspace write can be read back exactly for save verification", async () => {
    const root = await mkdtemp(join(tmpdir(), "manucodeai-auto-save-"));
    try {
        const target = join(root, "nested", "app.ts");
        const generated = "export const answer = 42;\n";
        await writeWorkspaceFile(target, generated);
        const saved = await readWorkspaceFile(target);
        assert.deepEqual(saved, { exists: true, content: generated });
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
