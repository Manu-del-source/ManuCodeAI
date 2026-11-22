/**
 * Per-file generation state for the streaming preview pipeline.
 *
 * Pure and vscode-free so it is fully unit-testable. The tracker is driven
 * by the AgentStreamRunner as chunks arrive and tracks, for every in-flight
 * `write_file` tool call:
 *
 *  - identity (tool call index, id, name)
 *  - the target workspace-relative path (accumulated incrementally)
 *  - the decoded content emitted so far
 *  - lifecycle status (streaming -> ready / cancelled / applied / rejected)
 *
 * It never touches the filesystem — the runner and the approval pipeline
 * own the actual file system boundaries.
 */

export type PreviewStatus =
    | "streaming"
    | "ready"
    | "cancelled"
    | "applied"
    | "rejected"
    | "closed";

export interface FilePreviewSession {
    /** Tool call index that produced this preview. */
    index: number;
    /** Backend tool call id (may be empty until it arrives). */
    id: string;
    /** Tool name, e.g. "write_file". */
    name: string;
    /** Workspace-relative path, normalized with forward slashes ("" until known). */
    path: string;
    /** Decoded content accumulated since the session started. */
    content: string;
    status: PreviewStatus;
    /**
     * The existing file's content captured when generation started
     * (null when the target does not exist — a new file).
     * Used to detect external modifications before approval.
     */
    baselineContent: string | null;
    /** True when the workspace file existed at baseline-capture time. */
    baselineExists: boolean;
    /** True once the baseline has been captured (content may legitimately be null/empty). */
    baselineCaptured: boolean;
    createdAt: number;
    updatedAt: number;
}

export function normalizePath(path: string): string {
    return path.trim().replace(/\\/g, "/").replace(/^\.\//, "");
}

export class PreviewTracker {
    private sessions = new Map<number, FilePreviewSession>();

    /** Create (or return) the session for a tool call. */
    start(index: number, id: string, name: string): FilePreviewSession {
        const existing = this.sessions.get(index);
        if (existing) {
            if (id && !existing.id) existing.id = id;
            if (name && !existing.name) existing.name = name;
            return existing;
        }
        const session: FilePreviewSession = {
            index,
            id: id || "",
            name: name || "write_file",
            path: "",
            content: "",
            status: "streaming",
            baselineContent: null,
            baselineExists: false,
            baselineCaptured: false,
            createdAt: Date.now(),
            updatedAt: Date.now(),
        };
        this.sessions.set(index, session);
        return session;
    }

    get(index: number): FilePreviewSession | undefined {
        return this.sessions.get(index);
    }

    list(): FilePreviewSession[] {
        return [...this.sessions.values()];
    }

    getByPath(path: string): FilePreviewSession | undefined {
        const normalized = normalizePath(path);
        for (const session of this.sessions.values()) {
            if (session.path === normalized) {
                return session;
            }
        }
        return undefined;
    }

    /** Append a decoded path fragment incrementally. */
    appendPath(index: number, delta: string): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session || !delta) return session;
        session.path = normalizePath(session.path + delta);
        session.updatedAt = Date.now();
        return session;
    }

    /** Set the target path (incrementally discovered during streaming). */
    setPath(index: number, rawPath: string): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        const normalized = normalizePath(rawPath);
        if (normalized) {
            session.path = normalized;
            session.updatedAt = Date.now();
        }
        return session;
    }

    /** Accumulate decoded content from the parser. */
    appendContent(index: number, delta: string): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session || !delta) return session;
        session.content += delta;
        session.updatedAt = Date.now();
        return session;
    }

    /** Store the original workspace file content captured at generation start. */
    setBaseline(index: number, content: string | null, exists: boolean): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        session.baselineContent = content;
        session.baselineExists = exists;
        session.baselineCaptured = true;
        return session;
    }

    /** Mark a session ready (stream completed, file awaiting review). */
    finish(index: number, path?: string, content?: string): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        if (path !== undefined && path.length > 0) session.path = normalizePath(path);
        if (content !== undefined) session.content = content;
        if (session.status === "streaming") session.status = "ready";
        session.updatedAt = Date.now();
        return session;
    }

    /** Mark a session cancelled (Stop pressed or stream aborted). */
    cancel(index: number): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        session.status = "cancelled";
        session.updatedAt = Date.now();
        return session;
    }

    /** Cancel every streaming session (e.g. Stop pressed mid-generation). */
    cancelAll(): FilePreviewSession[] {
        const cancelled: FilePreviewSession[] = [];
        for (const session of this.sessions.values()) {
            if (session.status === "streaming" || session.status === "ready") {
                session.status = "cancelled";
                session.updatedAt = Date.now();
                cancelled.push(session);
            }
        }
        return cancelled;
    }

    markApplied(index: number): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        if (session.status !== "cancelled" && session.status !== "rejected") {
            session.status = "applied";
        }
        session.updatedAt = Date.now();
        return session;
    }

    markRejected(index: number): FilePreviewSession | undefined {
        const session = this.sessions.get(index);
        if (!session) return undefined;
        if (session.status !== "cancelled" && session.status !== "applied") {
            session.status = "rejected";
        }
        session.updatedAt = Date.now();
        return session;
    }

    /** Remove all sessions (e.g. starting a new request). */
    reset(): void {
        this.sessions.clear();
    }
}
