/**
 * Pure SSE-stream/parsing layer — no VS Code dependency, fully unit-testable.
 *
 * Drives a PreviewTracker as the backend streams SSE events:
 *  - "delta"          assistant text (explanations)
 *  - "tool_call_start" a new tool call began (index/id/name)
 *  - "tool_call_delta" raw partial JSON argument fragments
 *  - "tool_call"      one complete tool call (parsed arguments)
 *  - "done"           stream finished
 *  - "cancelled"      stream was aborted
 *  - "error"          provider/backend error
 *
 * It emits StreamEvents to a callback and returns a RunResult at the end.
 * The VS Code shell (extension.ts) owns the webview, preview editor and
 * approval UI; this class only parses and tracks.
 */

import { PartialArgumentsParser } from "./partialArgumentsParser";
import { PreviewTracker, FilePreviewSession } from "./previewTracker";

export interface AgentToolCall {
    index?: number;
    id: string;
    name: string;
    arguments: Record<string, unknown>;
}

export type StreamEvent =
    | { type: "delta"; text: string }
    | { type: "fileStart"; session: FilePreviewSession }
    | { type: "filePath"; session: FilePreviewSession }
    | { type: "fileContent"; session: FilePreviewSession; delta: string }
    | { type: "fileDone"; session: FilePreviewSession }
    | { type: "done"; prose: string }
    | { type: "error"; message: string }
    | { type: "cancelled" };

export interface RunResult {
    prose: string;
    cancelled: boolean;
    error: string | null;
    toolCalls: AgentToolCall[];
    /** Sessions after the stream, in start order. */
    sessions: FilePreviewSession[];
}

export class AgentStreamRunner {
    readonly tracker = new PreviewTracker();
    private prose = "";
    private toolCalls: AgentToolCall[] = [];
    private parsers = new Map<number, PartialArgumentsParser>();
    private fileStarted = new Set<number>();
    private cancelled = false;
    private errorMessage: string | null = null;

    constructor(
        private readonly onEvent: (event: StreamEvent) => void | Promise<void> = () => {},
    ) {}

    /**
     * Consume a ReadableStream of SSE text. Resolves when the stream ends
     * (or aborts). Never throws for stream-level errors — they are returned
     * in the result.
     */
    async run(body: ReadableStream<Uint8Array>): Promise<RunResult> {
        const reader = body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                let sep;
                while ((sep = buffer.indexOf("\n\n")) !== -1) {
                    const block = buffer.slice(0, sep);
                    buffer = buffer.slice(sep + 2);
                    for (const evt of parseSseBlock(block)) {
                        await this.handleEvent(evt.name, evt.data);
                        if (this.cancelled) return this.finalize();
                    }
                }
            }

            // Flush any trailing buffered block.
            if (buffer.trim()) {
                for (const evt of parseSseBlock(buffer)) {
                    await this.handleEvent(evt.name, evt.data);
                    if (this.cancelled) return this.finalize();
                }
            }
        } catch (error) {
            // Reader aborted (Stop pressed) or connection dropped. Stay
            // cancelled — do NOT execute anything downstream.
            if (!this.cancelled && !this.errorMessage) {
                this.cancelled = true;
                this.tracker.cancelAll();
                await this.finalizeParsersForCancel();
                await this.onEvent({ type: "cancelled" });
            }
            return this.finalize();
        }

        // Stream ended without an explicit cancel; finalize any in-flight
        // write_file sessions (e.g. arguments arrived complete but the backend
        // did not emit a terminal event).
        for (const session of this.tracker.list()) {
            if (session.status === "streaming") {
                await this.finalizeSessionForParser(session.index);
                this.tracker.finish(session.index);
            }
        }

        return this.finalize();
    }

    private async handleEvent(name: string, dataStr: string): Promise<void> {
        let data: any = null;
        try {
            data = JSON.parse(dataStr);
        } catch {
            data = null;
        }

        if (name === "delta" && data && typeof data.content === "string" && data.content) {
            this.prose += data.content;
            await this.onEvent({ type: "delta", text: data.content });
            return;
        }

        if (name === "tool_call_start" && data) {
            const idx = typeof data.index === "number" ? data.index : 0;
            const id = typeof data.id === "string" ? data.id : "";
            const toolName = typeof data.name === "string" ? data.name : "";
            const session = this.tracker.start(idx, id, toolName);
            if (!this.parsers.has(idx)) this.parsers.set(idx, new PartialArgumentsParser());
            // Only announce file generation for write_file — the only tool
            // that produces generated source code.
            if (toolName === "write_file") {
                await this.onEvent({ type: "fileStart", session });
            }
            return;
        }

        if (name === "tool_call_delta" && data) {
            const idx = typeof data.index === "number" ? data.index : 0;
            const id = typeof data.id === "string" ? data.id : "";
            const toolName = typeof data.name === "string" ? data.name : "";
            // The name may be missing on the very first delta; merge from here.
            const prevName = this.tracker.get(idx)?.name || "";
            const session = this.tracker.start(idx, id || "", toolName || prevName);
            if (id && !session.id) session.id = id;
            if (toolName && session.name !== toolName) session.name = toolName;
            const effectiveName = toolName || session.name;

            // Only decode partial arguments for write_file (the only tool
            // that produces generated source code).
            if (effectiveName && effectiveName !== "write_file") return;

            // Announce the file once we know we are generating a file.
            if (!this.fileStarted.has(idx)) {
                this.fileStarted.add(idx);
                await this.onEvent({ type: "fileStart", session });
            }

            let parser = this.parsers.get(idx);
            if (!parser) {
                parser = new PartialArgumentsParser();
                this.parsers.set(idx, parser);
            }

            const out = parser.feed(typeof data.arguments_delta === "string" ? data.arguments_delta : "");

            if (out.pathDelta) {
                const before = session.path;
                this.tracker.appendPath(idx, out.pathDelta);
                const updated = this.tracker.get(idx);
                if (updated && updated.path !== before) {
                    await this.onEvent({ type: "filePath", session: updated });
                }
            }

            if (out.contentDelta) {
                const before = session.content;
                this.tracker.appendContent(idx, out.contentDelta);
                const updated = this.tracker.get(idx);
                if (updated && updated.content !== before) {
                    await this.onEvent({ type: "fileContent", session: updated, delta: out.contentDelta });
                }
            }
            return;
        }

        if (name === "tool_call" && data) {
            const idx = typeof data.index === "number" ? data.index : undefined;
            const id = typeof data.id === "string" ? data.id : "";
            const toolName = typeof data.name === "string" ? data.name : "";
            const args =
                data.arguments && typeof data.arguments === "object" && !Array.isArray(data.arguments)
                    ? (data.arguments as Record<string, unknown>)
                    : {};

            if (id && toolName) {
                this.toolCalls.push({ index: idx, id, name: toolName, arguments: args });
            }

            if (idx !== undefined && isWriteFileName(toolName)) {
                const content = typeof args.content === "string" ? args.content : undefined;
                const path = typeof args.path === "string" ? args.path : undefined;
                await this.finalizeSessionForParser(idx);
                const session = this.tracker.finish(idx, path, content);
                if (session) await this.onEvent({ type: "fileDone", session });
            }
            return;
        }

        if (name === "done" && data) {
            // Finalize any still-streaming write_file sessions defensively.
            for (const session of this.tracker.list()) {
                if (session.status === "streaming") {
                    await this.finalizeSessionForParser(session.index);
                    this.tracker.finish(session.index);
                }
            }
            await this.onEvent({ type: "done", prose: this.prose });
            return;
        }

        if (name === "error" && data && typeof data.message === "string") {
            this.errorMessage = data.message;
            this.tracker.cancelAll();
            await this.finalizeParsersForCancel();
            await this.onEvent({ type: "error", message: data.message });
            return;
        }

        if (name === "cancelled") {
            this.cancelled = true;
            this.tracker.cancelAll();
            await this.finalizeParsersForCancel();
            await this.onEvent({ type: "cancelled" });
            return;
        }
    }

    private async finalizeSessionForParser(index: number): Promise<void> {
        const parser = this.parsers.get(index);
        if (!parser) return;
        // Append whatever the parser has decoded to the session. The
        // authoritative content arrives via the complete tool_call event
        // (finish() overwrites); this is the defensive path for streams that
        // end mid-arguments.
        const flush = parser.flush();
        if (flush.contentDelta) {
            const session = this.tracker.appendContent(index, flush.contentDelta);
            if (session) await this.onEvent({ type: "fileContent", session, delta: flush.contentDelta });
        }
        if (flush.pathDelta) {
            const session = this.tracker.appendPath(index, flush.pathDelta);
            if (session) await this.onEvent({ type: "filePath", session });
        }
    }

    private async finalizeParsersForCancel(): Promise<void> {
        for (const index of [...this.parsers.keys()]) {
            await this.finalizeSessionForParser(index);
        }
        this.parsers.clear();
    }

    private finalize(): RunResult {
        return {
            prose: this.prose,
            cancelled: this.cancelled,
            error: this.errorMessage,
            toolCalls: this.toolCalls,
            sessions: this.tracker.list(),
        };
    }
}

function isWriteFileName(name: string): boolean {
    return name === "write_file";
}

/** Mirrors the Tolerant SSE framing the extension used before. */
function parseSseBlock(block: string): Array<{ name: string; data: string }> {
    const events: Array<{ name: string; data: string }> = [];
    const lines = block.split(/\r?\n/);
    let current: { name: string; dataLines: string[] } | null = null;

    const flush = (): void => {
        if (current && current.dataLines.length > 0) {
            events.push({ name: current.name, data: current.dataLines.join("\n") });
        }
        current = null;
    };

    for (const line of lines) {
        if (line.startsWith("event:")) {
            flush();
            current = { name: line.slice(6).trim() || "message", dataLines: [] };
        } else if (line.startsWith("data:")) {
            if (!current) current = { name: "message", dataLines: [] };
            current.dataLines.push(line.slice(5).trimStart());
        }
    }

    flush();
    return events;
}
