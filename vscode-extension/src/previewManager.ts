/**
 * Editor-side streaming preview manager.
 *
 * Creates untitled native VS Code documents, opens them in the editor area,
 * appends the genuine incremental chunks as they arrive, maintains separate
 * previews per file, and tracks each preview's generation state
 * (streaming → ready → cancelled → applied/rejected).
 *
 * Reveal speed is decoupled from stream speed: chunks handed to
 * `appendPreview` are queued in a per-file Typewriter and typed into the
 * editor at the configured human-like rate. The SSE reader keeps consuming
 * the backend stream at full speed — only the visual update is paced.
 *
 * The manager never touches workspace files — it only allocates untitled
 * in-memory documents. Workspace writes happen exclusively in the approval
 * path.
 */

import * as vscode from "vscode";
import { languageForFile } from "./core/language";
import { FilePreviewSession, PreviewStatus } from "./core/previewTracker";
import {
    DEFAULT_CHARS_PER_SECOND,
    Typewriter,
    resolveCharsPerSecond,
} from "./core/typewriter";

export interface ManagedPreview {
    /** Tool call index (stable id for the preview). */
    index: number;
    path: string;
    language: string;
    doc: vscode.TextDocument | undefined;
    editor: vscode.TextEditor | undefined;
    content: string;
    status: PreviewStatus;
    createdAt: number;
    updatedAt: number;
    /** Last time the end of the document was revealed (throttles scrolling). */
    lastRevealAt: number;
}

export class PreviewManager {
    private previews = new Map<number, ManagedPreview>();
    private typers = new Map<number, Typewriter>();
    private charsPerSecond = DEFAULT_CHARS_PER_SECOND;

    /**
     * Set the editor typing speed. Accepts a speed name ("slow" | "normal" |
     * "fast") or a custom characters-per-second number; invalid values fall
     * back to the default (35 cps). Live previews adopt the new rate for any
     * text that has not been revealed yet.
     */
    setTypingSpeed(value: unknown): void {
        this.charsPerSecond = resolveCharsPerSecond(value);
        for (const typer of this.typers.values()) {
            typer.setSpeed(this.charsPerSecond);
        }
    }

    /** Characters per second currently used for reveals. */
    get typingSpeed(): number {
        return this.charsPerSecond;
    }

    /**
     * Drop all previews from previous rounds/requests without closing or
     * re-opening documents (the documents are untitled and garbage-collected
     * by the host). Called when a new agent round starts.
     */
    clearStalePreviews(): void {
        this.cancelAllTyping();
        this.previews.clear();
    }

    get(index: number): ManagedPreview | undefined {
        return this.previews.get(index);
    }

    list(): ManagedPreview[] {
        return [...this.previews.values()];
    }

    /**
     * Get or create the preview for the given session. On first creation the
     * untitled document is opened in the editor area as a regular tab (so
     * multiple files keep their own tabs).
     */
    async ensurePreview(session: FilePreviewSession): Promise<ManagedPreview> {
        const existing = this.previews.get(session.index);
        if (existing) {
            this.applySessionPath(existing, session);
            return existing;
        }

        const preview: ManagedPreview = {
            index: session.index,
            path: session.path,
            language: session.path ? languageForFile(session.path) : "plaintext",
            doc: undefined,
            editor: undefined,
            content: "",
            status: "streaming",
            createdAt: Date.now(),
            updatedAt: Date.now(),
            lastRevealAt: 0,
        };

        try {
            preview.doc = await vscode.workspace.openTextDocument({
                language: preview.language,
                content: "",
            });
            preview.editor = await vscode.window.showTextDocument(preview.doc, {
                preview: false,
            });
        } catch (error) {
            // Doc creation can fail under strange hosts — keep the session so
            // the tracker/sidebar still reflects progress.
            console.warn("ManuCodeAI: failed to open preview document", error);
        }

        this.previews.set(session.index, preview);
        return preview;
    }

    /**
     * Queue a genuinely-decoded incremental chunk for the preview editor.
     *
     * The call returns as soon as the chunk is queued — it never waits for
     * the typing pace, so the stream reader is not back-pressured. The
     * queued text is typed into the document character-by-character (or in
     * very small chunks) by the preview's typewriter.
     */
    async appendPreview(index: number, delta: string): Promise<ManagedPreview | undefined> {
        const preview = this.previews.get(index);
        if (!preview || !delta) return preview;
        if (preview.status === "cancelled") return preview;

        this.typewriterFor(index).push(delta);
        return preview;
    }

    /**
     * Wait until every queued character of a preview has been revealed (or
     * the typing was cancelled). Used after the stream ends so the native
     * diff and the approval dialog always see the complete file.
     */
    async flushTyping(index: number): Promise<void> {
        const typer = this.typers.get(index);
        if (!typer) return;
        await typer.drain();
    }

    /**
     * Stop the visual typing immediately: drop every queued (not yet shown)
     * chunk and clear all pending reveal timers. Text already on screen is
     * kept so the incomplete preview stays visible.
     */
    cancelTyping(index?: number): void {
        if (index !== undefined) {
            this.typers.get(index)?.cancel();
            return;
        }
        for (const typer of this.typers.values()) {
            typer.cancel();
        }
    }

    /** Cancel typing and forget every typewriter (Stop / new round). */
    cancelAllTyping(): void {
        this.cancelTyping();
        this.typers.clear();
    }

    setStatus(index: number, status: PreviewStatus): ManagedPreview | undefined {
        const preview = this.previews.get(index);
        if (!preview) return undefined;
        preview.status = status;
        preview.updatedAt = Date.now();
        return preview;
    }

    /**
     * Close the preview's editor tab (finalize/cancel the preview). The
     * previews' untitled documents are garbage-collected by the host.
     */
    async closePreview(index: number): Promise<void> {
        const preview = this.previews.get(index);
        this.typers.get(index)?.cancel();
        this.typers.delete(index);
        if (!preview) return;
        this.previews.delete(index);
        const doc = preview.doc;
        if (doc) {
            await this.closeEditorTabFor(doc);
        }
    }

    /** Close and forget every preview (e.g. when a new agent round starts). */
    async closeAllPreviews(): Promise<void> {
        const keys = [...this.previews.keys()];
        for (const key of keys) {
            await this.closePreview(key);
        }
    }

    /** Mark a preview cancelled (Stop pressed) — keep the content visible. */
    markCancelled(index: number): void {
        this.cancelTyping(index);
        const preview = this.previews.get(index);
        if (!preview) return;
        preview.status = "cancelled";
        preview.updatedAt = Date.now();
    }

    /**
     * Cancel every open streaming preview (Stop pressed mid-generation):
     * pending typing is dropped first, then the previews are marked
     * cancelled.
     */
    markAllCancelled(): void {
        this.cancelAllTyping();
        for (const preview of this.previews.values()) {
            if (preview.status === "streaming" || preview.status === "ready") {
                preview.status = "cancelled";
            }
        }
    }

    /**
     * Replace the visible document content wholesale (backstop for
     * sessions whose chunks were never shown — e.g. content arrived only in
     * the final tool_call event).
     */
    async setContent(index: number, content: string): Promise<ManagedPreview | undefined> {
        const preview = this.previews.get(index);
        if (!preview) return undefined;
        // A wholesale replacement supersedes any queued typing.
        this.typers.get(index)?.cancel();
        preview.content = content;
        preview.updatedAt = Date.now();
        if (preview.doc && preview.editor) {
            try {
                const doc = preview.doc;
                const full = doc.getText();
                if (full !== content) {
                    const range = new vscode.Range(
                        0, 0,
                        Math.max(0, doc.lineCount - 1),
                        doc.lineCount > 0 ? doc.lineAt(doc.lineCount - 1).text.length : 0,
                    );
                    const ok = await preview.editor.edit((editBuilder) => {
                        editBuilder.replace(range, content);
                    });
                    if (ok) this.revealEnd(preview);
                }
            } catch {
                // Editor may have been closed — best effort.
            }
        }
        return preview;
    }

    /**
     * Lazily create the paced reveal for a preview. Each file has its own
     * queue/timer, so multiple files type into their own tabs concurrently.
     */
    private typewriterFor(index: number): Typewriter {
        const existing = this.typers.get(index);
        if (existing) return existing;

        const typer = new Typewriter({
            charsPerSecond: this.charsPerSecond,
            onType: (text) => this.revealChunk(index, text),
        });
        this.typers.set(index, typer);
        return typer;
    }

    /**
     * Append one revealed chunk to the preview document. `onType` is awaited
     * by the typewriter, so inserts are strictly ordered and never overlap.
     */
    private async revealChunk(index: number, text: string): Promise<void> {
        const preview = this.previews.get(index);
        if (!preview || preview.status === "cancelled") return;

        preview.content += text;
        preview.updatedAt = Date.now();

        if (!preview.doc || !preview.editor) return;

        try {
            const editor = preview.editor;
            const doc = preview.doc;
            const endPos = doc.positionAt(doc.getText().length);
            const ok = await editor.edit((editBuilder) => {
                editBuilder.insert(endPos, text);
            });
            // Typing fires many small edits per second; scrolling at the same
            // rate would be wasteful, so follow the cursor at most ~8x/s.
            if (ok && Date.now() - preview.lastRevealAt >= 120) {
                preview.lastRevealAt = Date.now();
                this.revealEnd(preview);
            }
        } catch {
            // The user closed the preview editor or the document — the
            // preview is best-effort.
        }
    }

    private applySessionPath(preview: ManagedPreview, session: FilePreviewSession): void {
        if (session.path && preview.path !== session.path) {
            preview.path = session.path;
            preview.language = languageForFile(session.path);
            preview.updatedAt = Date.now();
        }
    }

    private revealEnd(preview: ManagedPreview): void {
        const editor = preview.editor;
        const doc = preview.doc;
        if (!editor || !doc) return;
        try {
            const visible = editor.visibleRanges;
            if (visible && visible.length > 0) {
                const lastLine = doc.lineCount - 1;
                const atBottom = visible.some((r) => r.end.line >= lastLine - 1);
                if (!atBottom) return; // user scrolled away — don't yank the view
            }
            const endPos = doc.positionAt(doc.getText().length);
            editor.revealRange(new vscode.Range(endPos, endPos), vscode.TextEditorRevealType.InCenter);
        } catch {
            // Editor may have been closed — best effort.
        }
    }

    private async closeEditorTabFor(doc: vscode.TextDocument): Promise<void> {
        try {
            for (const group of vscode.window.tabGroups.all) {
                for (const tab of group.tabs) {
                    const input = tab.input as { uri?: vscode.Uri } | undefined;
                    const tabUri = input?.uri;
                    if (tabUri && tabUri.toString() === doc.uri.toString()) {
                        await vscode.window.tabGroups.close(tab, true); // preserveFocus
                        return;
                    }
                }
            }
        } catch {
            // Best-effort — the preview may stay as an untitled tab.
        }
    }
}
