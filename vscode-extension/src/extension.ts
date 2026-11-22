import * as vscode from "vscode";
import * as fs from "fs/promises";
import * as path from "path";
import { exec } from "child_process";
import { promisify } from "util";
import { randomUUID } from "crypto";
import {
    AgentStreamRunner,
    AgentToolCall,
    RunResult,
    StreamEvent,
} from "./core/agentStreamRunner";
import { FilePreviewSession } from "./core/previewTracker";
import {
    readWorkspaceFile,
    writeWorkspaceFile,
    detectExternalModification,
} from "./core/fileOps";
import {
    mapApprovalChoice,
    mapConflictChoice,
    decideApprovalOutcome,
    ApprovalDecision,
} from "./core/approval";
import { PreviewManager } from "./previewManager";

const execAsync = promisify(exec);

const API_URL = "http://127.0.0.1:8765";

const IGNORED_DIRECTORIES = new Set([
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "out",
    ".next",
    ".idea",
    ".vscode",
    "coverage",
    ".cache",
]);

const SECRET_NAMES = new Set([
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "secrets.json",
]);

const SECRET_EXTENSIONS = new Set([
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
]);

const MAX_FILE_SIZE = 150_000;
interface ToolResult {
    tool_call_id: string;
    output: string;
}

// ---------------------------------------------------------------------------
// Active editor tracker
// ---------------------------------------------------------------------------

class ActiveEditorTracker {
    private _onDidChangeListener: vscode.Disposable | undefined;
    private _activeEditor: vscode.TextEditor | undefined;

    constructor() {
        this._activeEditor = vscode.window.activeTextEditor;
        this._onDidChangeListener = vscode.window.onDidChangeActiveTextEditor((editor) => {
            this._activeEditor = editor;
        });
    }

    get activeEditor(): vscode.TextEditor | undefined {
        return this._activeEditor;
    }

    dispose(): void {
        this._onDidChangeListener?.dispose();
        this._onDidChangeListener = undefined;
        this._activeEditor = undefined;
    }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

class ManuCodeAIProvider implements vscode.WebviewViewProvider {
    private sessionId = randomUUID();
    private busy = false;
    private view: vscode.WebviewView | undefined;
    private abortController: AbortController | null = null;
    private editorTracker: ActiveEditorTracker;
    private previewManager: PreviewManager;
    private previewDir: string;

    constructor(private readonly context: vscode.ExtensionContext) {
        this.editorTracker = new ActiveEditorTracker();
        this.previewManager = new PreviewManager();
        this.previewDir = path.join(context.globalStorageUri.fsPath, "previews");
    }

    dispose(): void {
        this.editorTracker.dispose();
        this.abortController?.abort();
    }

    public newChat(): void {
        this.sessionId = randomUUID();
        this.abortController?.abort();
        this.previewManager.clearStalePreviews();
        if (this.view) {
            this.view.webview.postMessage({ command: "clear" });
            this.send(this.view, "System", "Started a new conversation.");
        }
    }

    public async sweepPreviewDirectory(): Promise<void> {
        try {
            await fs.rm(this.previewDir, { recursive: true, force: true });
            await fs.mkdir(this.previewDir, { recursive: true });
        } catch {
            // best-effort — previews are recreated as needed
        }
    }

    public resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;

        view.webview.options = {
            enableScripts: true,
        };

        view.webview.html = this.getHtml();

        view.webview.onDidReceiveMessage(async message => {
            try {
                await this.handleWebviewMessage(view, message);
            } catch (error) {
                // Never crash the extension host from the webview path.
                this.send(view, "Error", error instanceof Error ? error.message : String(error));
            }
        });
    }

    private async handleWebviewMessage(view: vscode.WebviewView, message: any): Promise<void> {
        if (message.command === "newChat") {
            this.newChat();
            await this.sweepPreviewDirectory();
            return;
        }

        if (message.command === "stop") {
            // Abort the backend stream and the previews' updates. The
            // preview tabs are marked incomplete — never executed.
            this.abortController?.abort();
            return;
        }

        if (message.command === "getActiveEditor") {
            const activeEditor = this.editorTracker.activeEditor;
            view.webview.postMessage({
                command: "activeEditorInfo",
                editor: activeEditor
                    ? {
                        uri: activeEditor.document.uri.fsPath,
                        languageId: activeEditor.document.languageId,
                        selection: activeEditor.selection,
                    }
                    : null,
            });
            return;
        }

        if (message.command === "selectText") {
            const { filePath, text } = message.params ?? {};
            if (filePath && text) {
                await this.selectTextInEditor(view, filePath, text);
            }
            return;
        }

        if (message.command !== "chat") return;

        if (this.busy) {
            this.send(view, "System", "Please wait for the current task to finish.");
            return;
        }

        const text = String(message.text || "").trim();
        if (!text) return;

        this.busy = true;
        this.abortController = new AbortController();

        view.webview.postMessage({ command: "busy", value: true });

        try {
            await this.runAgent(view, text);
        } catch (error) {
            if (error instanceof Error && error.name === "AbortError") {
                this.send(view, "System", "Generation stopped.");
            } else {
                this.send(view, "Error", error instanceof Error ? error.message : String(error));
            }
        } finally {
            this.busy = false;
            this.abortController = null;
            view.webview.postMessage({ command: "busy", value: false });
            view.webview.postMessage({ command: "ready" });
        }
    }

    private send(view: vscode.WebviewView, label: string, text: string): void {
        view.webview.postMessage({ command: "response", label, text });
    }

    private post(view: vscode.WebviewView, message: Record<string, unknown>): void {
        view.webview.postMessage(message);
    }

    // ------------------------------------------------------------------
    // Agent loop
    // ------------------------------------------------------------------

    private async runAgent(view: vscode.WebviewView, userMessage: string): Promise<void> {
        let nextMessage: string | null = userMessage;
        let toolResults: ToolResult[] = [];
        const MAX_ROUNDS = 10;

        for (let round = 0; round < MAX_ROUNDS; round++) {
            // Clear previews from earlier rounds; the preview the current
            // round owns is created fresh.
            this.previewManager.clearStalePreviews();
            view.webview.postMessage({ command: "assistantStart" });

            const result = await this.runStream(view, nextMessage, toolResults);
            nextMessage = null;
            toolResults = [];

            if (result.cancelled) {
                // Mark every preview cancelled and never execute queued tools.
                this.previewManager.markAllCancelled();
                view.webview.postMessage({ command: "assistantEnd" });
                return;
            }

            if (result.error) {
                this.previewManager.markAllCancelled();
                view.webview.postMessage({ command: "assistantEnd" });
                return;
            }

            const toolCalls = result.toolCalls;
            if (toolCalls.length === 0) {
                view.webview.postMessage({ command: "assistantEnd" });
                return;
            }

            for (const tool of toolCalls) {
                // Never execute queued write operations after cancellation.
                if (this.abortController?.signal.aborted) {
                    this.previewManager.markAllCancelled();
                    view.webview.postMessage({ command: "cancelled" });
                    view.webview.postMessage({ command: "assistantEnd" });
                    return;
                }

                this.send(view, "Progress", `Working: ${tool.name.replaceAll("_", " ")}`);

                let output: string;
                try {
                    output = await this.executeTool(view, tool, result);
                } catch (error) {
                    output = "Operation failed: " + (error instanceof Error ? error.message : String(error));
                }

                toolResults.push({ tool_call_id: tool.id, output });
            }

            view.webview.postMessage({ command: "assistantEnd" });
        }

        this.send(view, "System", "The agent reached its maximum operation limit. Review the progress above before continuing.");
    }

    // ------------------------------------------------------------------
    // SSE streaming
    // ------------------------------------------------------------------

    private async runStream(
        view: vscode.WebviewView,
        message: string | null,
        toolResults: ToolResult[],
    ): Promise<RunResult> {
        const payload = {
            session_id: this.sessionId,
            message,
            context: await this.getCompactWorkspaceContext(),
            tool_results: toolResults,
        };

        let response: globalThis.Response;
        try {
            response = await fetch(`${API_URL}/agent/stream`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
                signal: this.abortController?.signal,
            });
        } catch (error) {
            if (error instanceof Error && error.name === "AbortError") {
                // Stop pressed before the stream began — treat as cancelled.
                this.previewManager.markAllCancelled();
                return { prose: "", cancelled: true, error: null, toolCalls: [], sessions: [] };
            }
            throw error;
        }

        if (!response.ok || !response.body) {
            let detail = "Agent request failed.";
            try {
                const data = (await response.json()) as { detail?: string };
                if (data.detail) detail = data.detail;
            } catch {
                // ignore
            }
            // Treat as a hard error — mark previews cancelled and surface it.
            this.previewManager.markAllCancelled();
            this.send(view, "Error", detail);
            return { prose: "", cancelled: false, error: detail, toolCalls: [], sessions: [] };
        }

        const runner = new AgentStreamRunner(event => this.handleStreamEvent(view, event));
        const result = await runner.run(response.body);
        return result;
    }

    /** Runner callback — drives the sidebar, the previews and baseline capture. */
    private async handleStreamEvent(view: vscode.WebviewView, event: StreamEvent): Promise<void> {
        switch (event.type) {
            case "delta": {
                this.post(view, { command: "assistantDelta", text: event.text });
                return;
            }
            case "fileStart": {
                const session = event.session;
                this.post(view, { command: "currentFile", path: session.path || "", started: true });
                this.post(view, { command: "fileStart", path: session.path });
                return;
            }
            case "filePath": {
                const session = event.session;
                await this.previewManager.ensurePreview(session);
                await this.captureBaseline(session);
                this.post(view, { command: "currentFile", path: session.path });
                return;
            }
            case "fileContent": {
                const session = event.session;
                await this.previewManager.ensurePreview(session);
                await this.previewManager.appendPreview(session.index, event.delta);
                this.post(view, {
                    command: "codeProgress",
                    path: session.path,
                    bytes: session.content.length,
                    lines: session.content.split("\n").length,
                });
                return;
            }
            case "fileDone": {
                const session = event.session;
                await this.previewManager.ensurePreview(session);
                this.previewManager.setStatus(session.index, "ready");
                // Backstop: make any never-shown content visible.
                const preview = this.previewManager.get(session.index);
                const doc = preview?.doc;
                const shown = doc ? doc.getText() : "";
                if (preview && shown !== session.content && session.content.length > 0) {
                    await this.previewManager.setContent(session.index, session.content);
                }
                this.post(view, {
                    command: "fileDone",
                    path: session.path,
                    lines: session.content.split("\n").length,
                    bytes: session.content.length,
                });
                return;
            }
            case "done": {
                return;
            }
            case "error": {
                this.previewManager.markAllCancelled();
                this.post(view, { command: "generationError", message: event.message });
                return;
            }
            case "cancelled": {
                this.previewManager.markAllCancelled();
                this.post(view, { command: "generationStopped" });
                return;
            }
        }
    }

    /**
     * Capture the pre-generation state of the target file (baseline), the
     * moment its path is known. It is used to detect external modifications
     * before applying the proposed changes.
     */
    private async captureBaseline(session: FilePreviewSession): Promise<void> {
        if (session.baselineCaptured || !session.path) return;

        try {
            const target = await this.resolveWorkspacePath(session.path, true);
            const state = await readWorkspaceFile(target);
            session.baselineExists = state.exists;
            session.baselineContent = state.exists ? state.content : null;
            session.baselineCaptured = true;
        } catch {
            // Path may be outside the workspace — the approval step will
            // report the error; skip the baseline silently.
        }
    }

    /** Notify the webview of the file readiness (after the diff is shown). */
    private postFileReady(view: vscode.WebviewView, pathHint: string): void {
        this.post(view, { command: "fileReady", path: pathHint });
    }

    // ------------------------------------------------------------------
    // Tool execution
    // ------------------------------------------------------------------

    private async executeTool(
        view: vscode.WebviewView,
        tool: AgentToolCall,
        result: RunResult,
    ): Promise<string> {
        switch (tool.name) {
            case "list_files":
                return await this.listFiles(tool.arguments as { path?: string });

            case "read_file":
                return await this.readFile(tool.arguments as { path?: string });

            case "write_file":
                return await this.writeFile(view, tool, result);

            case "run_command":
                return await this.runCommand(view, tool.arguments as { command?: string });

            default:
                return `Unknown tool: ${tool.name}`;
        }
    }

    // ------------------------------------------------------------------
    // write_file: preview → diff → approval → apply
    // ------------------------------------------------------------------

    private async writeFile(
        view: vscode.WebviewView,
        tool: AgentToolCall,
        result: RunResult,
    ): Promise<string> {
        // The authoritative preview session from the stream.
        let session: FilePreviewSession | undefined;
        if (tool.index !== undefined) {
            session = result.sessions.find(s => s.index === tool.index);
        }
        // Fallback: match by path.
        if (!session && typeof tool.arguments.path === "string") {
            const wanted = tool.arguments.path.replace(/\\/g, "/").replace(/^\.\//, "");
            session = result.sessions.find(s => s.path === wanted);
        }

        const relPath = (session && session.path) || (typeof tool.arguments.path === "string" ? tool.arguments.path : "");
        const proposed = (session && session.content) || (typeof tool.arguments.content === "string" ? tool.arguments.content : "");

        if (!relPath) {
            return "The write_file tool requires a 'path' argument.";
        }

        const cancelled = session?.status === "cancelled";
        if (this.abortController?.signal.aborted || cancelled) {
            this.send(view, "System", `Generation of ${relPath} was cancelled.`);
            return `Generation of ${relPath} was cancelled.`;
        }

        const sessionContent = session ? session.content : proposed;

        return await this.approveAndApply(view, relPath, sessionContent, session);
    }

    private async approveAndApply(
        view: vscode.WebviewView,
        relPath: string,
        proposed: string,
        session: FilePreviewSession | undefined,
    ): Promise<string> {
        let target: string;
        try {
            target = await this.resolveWorkspacePath(relPath, true);
        } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            this.send(view, "Error", detail);
            return `Cannot write ${relPath}: ${detail}`;
        }

        // Baseline: captured during generation when possible.
        let baselineExists: boolean;
        let baselineContent: string | null;
        if (session?.baselineCaptured) {
            baselineExists = session.baselineExists;
            baselineContent = session.baselineContent;
        } else {
            // Fallback: capture as-is — no external-modification detection.
            const current = await readWorkspaceFile(target);
            baselineExists = current.exists;
            baselineContent = current.exists ? current.content : null;
        }

        // Open a native diff between the original and the proposed content.
        const temp = await this.buildDiff(target, relPath, baselineExists, proposed);
        try {
            const existing = baselineExists;
            const choice = await vscode.window.showWarningMessage(
                existing
                    ? `Modify ${relPath}? Review the diff before approving.`
                    : `Create ${relPath}? Review the diff before approving.`,
                { modal: true },
                existing ? "Apply changes" : "Apply",
                "Reject",
            );
            const approvalChoice = mapApprovalChoice(choice);

            const current = await readWorkspaceFile(target);
            const conflict = detectExternalModification(
                { exists: baselineExists, content: baselineContent ?? "" },
                current,
                relPath,
            );

            let conflictChoice: ApprovalDecision | null = null;

            if (conflict && approvalChoice === "apply") {
                const conflictChoiceUI = await vscode.window.showWarningMessage(
                    `${conflict} Apply changes anyway?`,
                    { modal: true },
                    "Apply anyway",
                    "Cancel",
                );
                conflictChoice = mapConflictChoice(conflictChoiceUI);
            }

            const outcome = decideApprovalOutcome({
                baselineExists,
                baselineContent,
                currentExists: current.exists,
                currentContent: current.content,
                approvalChoice: approvalChoice,
                conflictChoice: conflictChoice || null,
            });

            if (outcome.decision === "apply") {
                this.postFileReady(view, relPath);
                await writeWorkspaceFile(target, proposed);
                if (session) {
                    session.status = "applied";
                    this.previewManager.setStatus(session.index, "applied");
                    await this.previewManager.closePreview(session.index);
                }
                const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target));
                await vscode.window.showTextDocument(document, { preview: false });
                const size = proposed.length;
                this.send(
                    view,
                    "Progress",
                    `Saved ${relPath} (${size} characters). The file was opened in VS Code.`,
                );
                return `Saved ${relPath} (${size} characters). The file was opened in VS Code.`;
            }

            if (outcome.decision === "reject") {
                if (session) {
                    session.status = "rejected";
                    this.previewManager.setStatus(session.index, "rejected");
                    await this.previewManager.closePreview(session.index);
                }
                this.send(view, "System", `Changes to ${relPath} were rejected. No file was modified.`);
                return `Changes to ${relPath} were rejected. No file was modified.`;
            }

            // cancel
            if (session) {
                this.previewManager.markCancelled(session.index);
            }
            this.send(view, "System", `Changes to ${relPath} were cancelled. No file was modified.`);
            return `Changes to ${relPath} were cancelled. No file was modified.`;
        } finally {
            // Clean up the proposals' temp-diff files (the diff view retains its own copy).
            for (const file of temp) {
                await fs.rm(file, { force: true }).catch(() => {});
            }
        }
    }

    /** Build the diff: workspace original vs proposed temp file. */
    private async buildDiff(
        target: string,
        relPath: string,
        existed: boolean,
        proposed: string,
    ): Promise<string[]> {
        const tempFiles: string[] = [];

        try {
            await fs.mkdir(this.previewDir, { recursive: true });

            const id = randomUUID();
            const ext = path.extname(target);
            const newTemp = path.join(this.previewDir, `${id}-new${ext}`);
            await fs.writeFile(newTemp, proposed, "utf8");
            tempFiles.push(newTemp);

            let oldUri: vscode.Uri;
            if (existed) {
                oldUri = vscode.Uri.file(target);
            } else {
                const oldTemp = path.join(this.previewDir, `${id}-old.tmp`);
                await fs.writeFile(oldTemp, "", "utf8");
                tempFiles.push(oldTemp);
                oldUri = vscode.Uri.file(oldTemp);
            }

            await vscode.commands.executeCommand(
                "vscode.diff",
                oldUri,
                vscode.Uri.file(newTemp),
                `Proposed changes: ${relPath}`,
            );
        } catch (error) {
            // The diff must not block the approval flow — if it fails we
            // still let the user decide.
            void error;
        }

        return tempFiles;
    }

    // ------------------------------------------------------------------
    // Tools
    // ------------------------------------------------------------------

    private async listFiles(args: { path?: string }): Promise<string> {
        const relPath = typeof args?.path === "string" ? args.path.trim() : "";
        const root = this.getWorkspaceRoot();
        const targetDir = relPath ? await this.resolveWorkspacePath(relPath, true) : root;

        let stat;
        try {
            stat = await fs.stat(targetDir);
        } catch {
            throw new Error(`Directory does not exist: ${relPath || "."}`);
        }
        if (!stat.isDirectory()) {
            throw new Error(`Not a directory: ${relPath || "."}`);
        }

        const files: string[] = [];
        const MAX_FILES = 100;

        async function scan(directory: string): Promise<void> {
            if (files.length >= MAX_FILES) return;

            let entries;
            try {
                entries = await fs.readdir(directory, { withFileTypes: true });
            } catch {
                return;
            }

            for (const entry of entries) {
                if (files.length >= MAX_FILES) break;

                if (IGNORED_DIRECTORIES.has(entry.name)) continue;
                if (entry.isSymbolicLink()) continue;

                if (
                    SECRET_NAMES.has(entry.name.toLowerCase()) ||
                    entry.name.toLowerCase().startsWith(".env")
                ) {
                    continue;
                }

                const fullPath = path.join(directory, entry.name);
                const relative = path.relative(root, fullPath).replace(/\\/g, "/");

                if (entry.isDirectory()) {
                    files.push(`${relative}/`);
                    await scan(fullPath);
                } else if (entry.isFile()) {
                    files.push(relative);
                }
            }
        }

        await scan(targetDir);

        if (files.length === 0) {
            return `No files found under ${relPath || "."}.`;
        }

        return files.join("\n");
    }

    private async readFile(args: { path?: string }): Promise<string> {
        const relPath = typeof args?.path === "string" ? args.path.trim() : "";
        if (!relPath) {
            throw new Error("read_file requires a 'path' argument.");
        }

        const target = await this.resolveWorkspacePath(relPath, false);
        const stat = await fs.stat(target);

        if (!stat.isFile()) {
            throw new Error(`Not a file: ${relPath}`);
        }

        if (stat.size > MAX_FILE_SIZE) {
            throw new Error(`File is too large to read (${stat.size} bytes): ${relPath}`);
        }

        return await fs.readFile(target, "utf8");
    }

    private async runCommand(
        view: vscode.WebviewView,
        args: { command?: string },
    ): Promise<string> {
        const command = typeof args?.command === "string" ? args.command.trim() : "";
        if (!command) {
            throw new Error("run_command requires a 'command' argument.");
        }

        const workspaceRoot = this.getWorkspaceRoot();

        const choice = await vscode.window.showWarningMessage(
            `Execute this command in the workspace?\n\n${command}`,
            { modal: true },
            "Run",
            "Cancel",
        );

        if (choice !== "Run") {
            return "Command rejected by the user.";
        }

        try {
            const { stdout, stderr } = await execAsync(command, {
                cwd: workspaceRoot,
                timeout: 120_000,
                maxBuffer: 1024 * 1024,
                windowsHide: true,
            });

            const output = [
                stdout?.trim(),
                stderr?.trim() ? `stderr: ${stderr.trim()}` : "",
            ]
                .filter(Boolean)
                .join("\n")
                .slice(0, 8000);

            return output || "(no output)";
        } catch (error) {
            const err = error as { stdout?: string; stderr?: string; message?: string };
            const output = [
                err.stdout?.trim(),
                err.stderr?.trim() ? `stderr: ${err.stderr.trim()}` : "",
                err.message ? `Error: ${err.message}` : "",
            ]
                .filter(Boolean)
                .join("\n")
                .slice(0, 8000);

            return output || "Command failed with no details.";
        }
    }

    private async selectTextInEditor(
        view: vscode.WebviewView,
        filePath: string,
        text: string,
    ): Promise<void> {
        const fullPath = await this.resolveWorkspacePath(filePath, true);

        try {
            const document = await vscode.workspace.openTextDocument(vscode.Uri.file(fullPath));
            const editor = await vscode.window.showTextDocument(document, {
                preview: false,
            });

            const fullText = document.getText();
            const matchIndex = fullText.indexOf(text);

            if (matchIndex >= 0) {
                const startPos = document.positionAt(matchIndex);
                const endPos = document.positionAt(matchIndex + text.length);

                editor.selection = new vscode.Selection(startPos, endPos);
                editor.revealRange(new vscode.Range(startPos, endPos), vscode.TextEditorRevealType.InCenter);
                this.send(view, "Progress", `Selected text in ${filePath}.`);
            } else {
                this.send(view, "Error", `Text not found in ${filePath}.`);
            }
        } catch (error) {
            this.send(view, "Error", `Failed to select text: ${error instanceof Error ? error.message : String(error)}`);
        }
    }

    // ------------------------------------------------------------------
    // Paths / context
    // ------------------------------------------------------------------

    private getWorkspaceRoot(): string {
        const folders = vscode.workspace.workspaceFolders;

        if (!folders || folders.length === 0) {
            throw new Error("Open a project folder in VS Code first.");
        }

        return folders[0].uri.fsPath;
    }

    private isSecretPath(relativePath: string): boolean {
        const parts = relativePath
            .replace(/\\/g, "/")
            .split("/")
            .filter(Boolean);

        return parts.some(part => {
            const lower = part.toLowerCase();

            return (
                SECRET_NAMES.has(lower) ||
                SECRET_EXTENSIONS.has(path.extname(lower)) ||
                lower.startsWith(".env.")
            );
        });
    }

    private async resolveWorkspacePath(relativePath: string, allowMissing: boolean): Promise<string> {
        const root = await fs.realpath(this.getWorkspaceRoot());
        const cleaned = relativePath.replace(/\\/g, "/");

        if (
            !cleaned ||
            path.isAbsolute(cleaned) ||
            /^[a-zA-Z]:/.test(cleaned) ||
            cleaned.split("/").includes("..")
        ) {
            throw new Error("Invalid workspace-relative path.");
        }

        if (this.isSecretPath(cleaned)) {
            throw new Error("Access to secret or credential files is blocked.");
        }

        const target = path.resolve(root, cleaned);
        const relative = path.relative(root, target);

        if (
            relative === ".." ||
            relative.startsWith(".." + path.sep) ||
            path.isAbsolute(relative)
        ) {
            throw new Error("The requested path is outside the workspace.");
        }

        let existingPath = target;

        while (true) {
            try {
                await fs.lstat(existingPath);
                break;
            } catch {
                if (!allowMissing) {
                    throw new Error("File does not exist.");
                }

                const parent = path.dirname(existingPath);

                if (parent === existingPath) {
                    throw new Error("Could not resolve the requested path.");
                }

                existingPath = parent;
            }
        }

        const realExisting = await fs.realpath(existingPath);
        const realRelative = path.relative(root, realExisting);

        if (
            realRelative === ".." ||
            realRelative.startsWith(".." + path.sep) ||
            path.isAbsolute(realRelative)
        ) {
            throw new Error("The requested path resolves outside the workspace.");
        }

        return target;
    }

    private async getCompactWorkspaceContext(): Promise<string> {
        const root = this.getWorkspaceRoot();
        const files: string[] = [];
        const MAX_FILES = 100;

        async function scan(directory: string): Promise<void> {
            if (files.length >= MAX_FILES) return;

            let entries;

            try {
                entries = await fs.readdir(directory, { withFileTypes: true });
            } catch {
                return;
            }

            for (const entry of entries) {
                if (files.length >= MAX_FILES) break;

                if (IGNORED_DIRECTORIES.has(entry.name)) continue;

                if (entry.isSymbolicLink()) continue;

                const fullPath = path.join(directory, entry.name);
                const relative = path.relative(root, fullPath).replace(/\\/g, "/");

                if (
                    SECRET_NAMES.has(entry.name.toLowerCase()) ||
                    entry.name.toLowerCase().startsWith(".env")
                ) {
                    continue;
                }

                if (entry.isDirectory()) {
                    files.push(`${relative}/`);
                    await scan(fullPath);
                } else if (entry.isFile()) {
                    files.push(relative);
                }
            }
        }

        await scan(root);

        return [
            `Workspace root: ${root}`,
            `Project files (maximum ${MAX_FILES}):`,
            ...files.map(file => `- ${file}`),
        ].join("\n");
    }

    // ------------------------------------------------------------------
    // Webview HTML
    // ------------------------------------------------------------------

    private getHtml(): string {
        const nonce = randomUUID().replace(/-/g, "");

        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">

<style>
    body {
        font-family: var(--vscode-font-family);
        color: var(--vscode-foreground);
        padding: 12px;
        background: var(--vscode-sideBar-background);
    }

    h2 {
        color: #D5AD65;
        margin: 0 0 4px 0;
    }

    .panel {
        margin: 16px 0;
        padding: 12px;
        border-radius: 6px;
        background: var(--vscode-editor-inactiveSelectionBackground);
        border: 1px solid var(--vscode-widget-border);
    }

    .panel h3 {
        margin: 0 0 8px 0;
        color: #D5AD65;
        font-size: 14px;
    }

    .panel p {
        margin: 4px 0;
        font-size: 13px;
        line-height: 1.4;
    }

    .panel .dimmed {
        color: var(--vscode-descriptionForeground);
        font-size: 12px;
    }

    .progress-bar {
        width: 100%;
        height: 4px;
        background: var(--vscode-progressBar-background);
        border-radius: 2px;
        overflow: hidden;
        margin: 8px 0;
    }

    .progress-fill {
        height: 100%;
        background: #B8934A;
        width: 0%;
        transition: width 0.3s ease;
    }

    .command-input {
        width: 100%;
        padding: 8px;
        margin: 8px 0;
        box-sizing: border-box;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        font-family: var(--vscode-font-family);
        font-size: 13px;
        min-height: 40px;
        resize: vertical;
    }

    .action-button {
        padding: 8px 16px;
        margin: 4px;
        cursor: pointer;
        background: #B8934A;
        color: #111;
        border: none;
        font-weight: bold;
        font-size: 13px;
        border-radius: 4px;
    }

    .action-button:hover {
        background: #a78740;
    }

    .action-button.secondary {
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
    }

    .action-button.danger {
        background: #a33;
        color: #fff;
    }

    .action-button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .current-file {
        font-family: var(--vscode-editor-font-family, monospace);
        font-size: 12px;
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        border: 1px solid var(--vscode-widget-border);
        border-radius: 4px;
        padding: 8px 10px;
        margin: 8px 0;
        word-break: break-all;
        white-space: pre-wrap;
    }

    .instruction-item {
        padding: 8px 12px;
        margin: 4px 0;
        background: var(--vscode-editor-background);
        border-radius: 4px;
        border-left: 3px solid #D5AD65;
    }

    .status {
        padding: 8px 10px;
        margin: 6px 0;
        border-radius: 6px;
        background: var(--vscode-editor-selectionBackground);
        font-style: italic;
        opacity: 0.9;
        font-size: 12px;
    }

    .hint {
        color: var(--vscode-descriptionForeground);
        font-size: 12px;
        margin-top: 12px;
    }
</style>
</head>

<body>

<h2>ManuCodeAI</h2>
<p>AI Coding Agent — generated code streams directly into editor tabs.</p>

<div class="panel" id="panel">
    <h3>Instructions</h3>
    <p>Enter your coding task below. Generated code streams progressively into editor tabs.</p>

    <textarea id="input" class="command-input" placeholder="Describe what you want to build or change..." rows="3"></textarea>

    <div style="margin-top: 12px;">
        <button class="action-button" id="send">Send</button>
        <button class="action-button danger secondary" id="stop" disabled>Stop</button>
    </div>

    <p class="hint">Code appears in your editor — never in the chat. You approve the changes before anything is written.</p>
</div>

<div class="panel" id="progressPanel" style="display:none;">
    <h3>Generation</h3>
    <div class="current-file" id="currentFile">—</div>
    <div class="progress-bar">
        <div class="progress-fill" id="progressFill"></div>
    </div>
    <span id="progressText">Idle</span>
</div>

<div class="panel" id="sidebar">
    <h3>Sidebar</h3>
    <div id="sidebarContent">
        <p>Waiting for instruction...</p>
    </div>
</div>

<div class="panel" id="activity">
    <h3>Activity</h3>
    <div id="activityLog">
        <p>No activity yet.</p>
    </div>
</div>

<script nonce="${nonce}">
const vscode = acquireVsCodeApi();

const state = {
    busy: false,
    liveAssistant: null,
    currentFile: null
};

const input = document.getElementById('input');
const send = document.getElementById('send');
const stop = document.getElementById('stop');
const sidebarContent = document.getElementById('sidebarContent');
const progressPanel = document.getElementById('progressPanel');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const currentFileEl = document.getElementById('currentFile');
const activityLog = document.getElementById('activityLog');

function scrollToBottom() {
    const containers = [sidebarContent, activityLog];
    for (const container of containers) {
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }
}

function addSidebarLine(text, className) {
    const div = document.createElement('div');
    div.className = 'instruction-item' + (className ? ' ' + className : '');
    div.textContent = text;
    sidebarContent.insertBefore(div, sidebarContent.firstChild);
    scrollToBottom();
}

function ensureLiveAssistant() {
    if (!state.liveAssistant) {
        state.liveAssistant = document.createElement('div');
        state.liveAssistant.className = 'instruction-item assistant';
        state.liveAssistant.textContent = 'ManuCodeAI: ';
        sidebarContent.insertBefore(state.liveAssistant, sidebarContent.firstChild);
    }
    return state.liveAssistant;
}

function appendLiveAssistant(text) {
    const el = ensureLiveAssistant();
    el.textContent += text;
    scrollToBottom();
}

function finishLiveAssistant() {
    state.liveAssistant = null;
}

function addActivityLine(text) {
    const div = document.createElement('div');
    div.className = 'dimmed';
    div.textContent = text;
    activityLog.insertBefore(div, activityLog.firstChild);
    scrollToBottom();
}

function setBusy(busy) {
    state.busy = busy;
    send.disabled = busy;
    stop.disabled = !busy;
    send.textContent = busy ? 'Working...' : 'Send';
    progressPanel.style.display = busy ? 'block' : 'none';
}

function setProgress(text, percent) {
    progressText.textContent = text;
    if (typeof percent === 'number') {
        progressFill.style.width = percent + '%';
    } else {
        progressFill.style.width = '100%';
    }
}

function setCurrentFile(path) {
    state.currentFile = path || null;
    currentFileEl.textContent = path || '—';
}

function sendMessage() {
    const text = input.value.trim();
    if (!text || state.busy) return;

    addSidebarLine('You: ' + text, '');
    setBusy(true);

    vscode.postMessage({
        command: 'chat',
        text: text
    });

    input.value = '';
}

send.addEventListener('click', sendMessage);

input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

stop.addEventListener('click', () => {
    vscode.postMessage({ command: 'stop' });
    setBusy(false);
    setCurrentFile(null);
});

window.addEventListener('message', event => {
    const data = event.data || {};

    if (data.command === 'response') {
        finishLiveAssistant();
        addSidebarLine('ManuCodeAI: ' + data.text, 'assistant');
        setBusy(false);
    }

    if (data.command === 'assistantDelta') {
        appendLiveAssistant(data.text);
    }

    if (data.command === 'assistantStart') {
        finishLiveAssistant();
        setBusy(true);
        addActivityLine('ManuCodeAI is starting work...');
    }

    if (data.command === 'assistantEnd') {
        finishLiveAssistant();
        setBusy(false);
        addSidebarLine('ManuCodeAI finished.', 'dimmed');
        setCurrentFile(null);
        progressFill.style.width = '0%';
    }

    if (data.command === 'busy') {
        setBusy(data.value);
    }

    if (data.command === 'ready') {
        send.disabled = false;
        stop.disabled = true;
        send.textContent = 'Send';
        setBusy(false);
    }

    if (data.command === 'currentFile') {
        setCurrentFile(data.path);
    }

    if (data.command === 'fileStart') {
        addActivityLine('Generating file...');
    }

    if (data.command === 'codeProgress') {
        const path = state.currentFile || data.path || '';
        setBusy(true);
        setCurrentFile(path);
        const lines = data.lines || 0;
        const bytes = data.bytes || 0;
        setProgress(path + '  ·  ' + lines + ' lines · ' + bytes + ' chars', null);
    }

    if (data.command === 'fileDone') {
        const path = state.currentFile || data.path || '';
        setBusy(false);
        addActivityLine('File ready for review: ' + path + ' (' + (data.lines || 0) + ' lines, ' + (data.bytes || 0) + ' chars)');
        setProgress('Ready: ' + path, 100);
    }

    if (data.command === 'fileReady') {
        addActivityLine('Diff opened: ' + data.path + ' — awaiting your approval');
        setProgress('Awaiting approval: ' + data.path, 100);
    }

    if (data.command === 'generationStopped') {
        finishLiveAssistant();
        setBusy(false);
        setCurrentFile(null);
        addActivityLine('Generation stopped — incomplete preview kept in the editor');
        setProgress('Stopped', 0);
    }

    if (data.command === 'generationError') {
        finishLiveAssistant();
        setBusy(false);
        addActivityLine('Error: ' + (data.message || 'unknown'));
        setProgress('Error', 0);
    }

    if (data.command === 'fileChange') {
        addActivityLine('Writing: ' + data.filePath);
    }

    if (data.command === 'toolProgress') {
        addActivityLine('Working: ' + (data.name || 'tool') + (data.filePath ? ' → ' + data.filePath : ''));
    }

    if (data.command === 'activeEditorInfo') {
        const editorInfo = data.editor;
        if (editorInfo) {
            addSidebarLine('Active editor: ' + editorInfo.uri, 'dimmed');
        }
    }

    if (data.command === 'clear') {
        sidebarContent.replaceChildren();
        activityLog.replaceChildren();
        addSidebarLine('Starting new conversation...', '');
        addActivityLine('No activity yet.');
        state.liveAssistant = null;
        state.currentFile = null;
        currentFileEl.textContent = '—';
        setBusy(false);
    }
});
</script>

</body>
</html>`;
    }
}

export function activate(context: vscode.ExtensionContext): void {
    const provider = new ManuCodeAIProvider(context);

    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            "manucodeai.chat",
            provider,
            { webviewOptions: { retainContextWhenHidden: true } }
        ),
        vscode.commands.registerCommand("manucodeai.newChat", () => provider.newChat()),
        { dispose: () => provider.dispose() }
    );

    // Sweep any previews from a previous session.
    provider.sweepPreviewDirectory();
}

export function deactivate(): void {
    // Disposables in context.subscriptions are cleaned up automatically.
}
