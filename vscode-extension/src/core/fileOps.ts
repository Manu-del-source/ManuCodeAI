/**
 * Pure workspace-file operations (no VS Code dependency).
 *
 * The extension owns the approval/UI step; these functions are the only
 * writer of workspace files. They are deliberately narrow:
 *  - readWorkspaceFile: read the current content + existence (for diffs and
 *    conflict detection)
 *  - writeWorkspaceFile: create/replace a file (only ever called by the
 *    approval path — never before approval)
 *  - detectExternalModification: compare a baseline against the current state
 *
 * All semantics operate on absolute paths; callers are responsible for
 * resolving the target path safely inside the workspace.
 */

import * as fs from "fs/promises";
import * as path from "path";

export interface FileState {
    exists: boolean;
    content: string;
}

export async function readWorkspaceFile(absPath: string): Promise<FileState> {
    try {
        const stat = await fs.stat(absPath);
        if (!stat.isFile()) {
            return { exists: false, content: "" };
        }
        const content = await fs.readFile(absPath, "utf8");
        return { exists: true, content };
    } catch (error: any) {
        if (error && error.code === "ENOENT") {
            return { exists: false, content: "" };
        }
        throw error;
    }
}

export async function writeWorkspaceFile(absPath: string, content: string): Promise<void> {
    const parent = path.dirname(absPath);
    await fs.mkdir(parent, { recursive: true });
    await fs.writeFile(absPath, content, "utf8");
}

/**
 * Detect whether the workspace file changed between baseline capture and
 * approval time. `baseline` is the state captured when generation started;
 * `current` is the state read at approval time.
 *
 * Returns a human-readable reason when a conflict is detected, or null when
 * the file is safe to overwrite.
 */
export function detectExternalModification(
    baseline: FileState,
    current: FileState,
    displayPath: string,
): string | null {
    if (!baseline.exists && current.exists) {
        return `${displayPath} was created externally since generation started.`;
    }
    if (baseline.exists && !current.exists) {
        return `${displayPath} was deleted externally since generation started.`;
    }
    if (baseline.exists && current.exists && baseline.content !== current.content) {
        return `${displayPath} was modified since generation started.`;
    }
    return null;
}
