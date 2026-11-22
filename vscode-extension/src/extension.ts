import * as vscode from "vscode";
import * as fs from "fs/promises";
import * as path from "path";
import { exec } from "child_process";
import { promisify } from "util";
import { randomUUID } from "crypto";

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
const MAX_CONTENT_SIZE = 500_000;

interface AgentToolCall {
    id: string;
    name: string;
    arguments: Record<string, unknown>;
}

interface AgentResponse {
    response: string;
    tool_calls: AgentToolCall[];
    finished: boolean;
}

interface ToolResult {
    tool_call_id: string;
    output: string;
}

interface CodeChange {
    type: "insert" | "modify" | "delete";
    line: number;
    text?: string;
}

interface CodeDiff {
    original: string;
    modified: string;
    changes: CodeChange[];
}

// Track active editor
class ActiveEditorTracker {
    private _onDidChangeListener: vscode.Disposable | undefined;
    private _activeEditor: vscode.TextEditor | undefined;

    constructor() {
        this._activeEditor = vscode.window.activeTextEditor;
        this._onDidChangeListener = vscode.window.onDidChangeActiveTextEditor(
            (editor) => {
                this._activeEditor = editor;
            }
        );
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

// Generate diff between original and new content
function generateDiff(original: string, newContent: string): CodeDiff {
    const originalLines = original.split("\n");
    const newLines = newContent.split("\n");
    const changes: CodeChange[] = [];

    let origIdx = 0;
    let newIdx = 0;
    const numOriginal = originalLines.length;
    const numNew = newLines.length;

    while (origIdx < numOriginal || newIdx < numNew) {
        const origLine = origIdx < numOriginal ? originalLines[origIdx] : null;
        const newLine = newIdx < numNew ? newLines[newIdx] : null;

        if (origLine === null && newLine === null) break;
        if (origLine === null) {
            if (newLine !== null) {
                changes.push({ type: "insert", line: origIdx, text: newLine });
            }
            newIdx++;
        } else if (newLine === null) {
            changes.push({ type: "delete", line: origIdx });
            origIdx++;
        } else if (origLine !== newLine) {
            changes.push({ type: "modify", line: origIdx, text: newLine });
            origIdx++;
            newIdx++;
        } else {
            origIdx++;
            newIdx++;
        }
    }

    return { original, modified: newContent, changes };
}

// Create TextEdit for a code change
function createTextEdit(change: CodeChange, original: string): vscode.TextEdit | null {
    const lines = original.split("\n");
    const lineIndex = change.line;

    if (lineIndex < 0 || lineIndex >= lines.length) {
        return null;
    }

    const startPos = new vscode.Position(lineIndex, 0);
    const endLine = lines[lineIndex];
    const endPos = new vscode.Position(lineIndex, endLine.length);

    if (change.type === "delete") {
        return new vscode.TextEdit(new vscode.Range(startPos, endPos), "");
    }

    if (change.type === "insert") {
        return new vscode.TextEdit(
            new vscode.Range(new vscode.Position(lineIndex, 0), new vscode.Position(lineIndex, 0)),
            change.text || ""
        );
    }

    if (change.type === "modify") {
        return new vscode.TextEdit(new vscode.Range(startPos, endPos), change.text || "");
    }

    return null;
}

// Apply workspace edits (line-based, safe for insertion/deletion)
function applyWorkspaceEdits(original: string, changes: CodeChange[]): { newContent: string; edits: vscode.TextEdit[] } {
    const edits: vscode.TextEdit[] = [];
    const sortedChanges = [...changes].sort((a, b) => b.line - a.line);
    const result = original.split("\n");

    for (const change of sortedChanges) {
        const edit = createTextEdit(change, original);
        if (edit) {
            edits.push(edit);
        }

        if (change.type === "insert") {
            const insertLine = Math.min(Math.max(change.line, 0), result.length);
            result.splice(insertLine, 0, change.text ?? "");
        } else if (change.type === "delete") {
            if (change.line >= 0 && change.line < result.length) {
                result.splice(change.line, 1);
            }
        } else if (change.type === "modify") {
            if (change.line >= 0 && change.line < result.length) {
                result[change.line] = change.text ?? "";
            }
        }
    }

    return { newContent: result.join("\n"), edits };
}

class ManuCodeAIProvider implements vscode.WebviewViewProvider {
    private sessionId = randomUUID();
    private busy = false;
    private view: vscode.WebviewView | undefined;
    private abortController: AbortController | null = null;
    private editorTracker: ActiveEditorTracker;

    constructor(private readonly context: vscode.ExtensionContext) {
        this.editorTracker = new ActiveEditorTracker();
    }

    dispose(): void {
        this.editorTracker.dispose();
    }

    public newChat(): void {
        this.sessionId = randomUUID();
        this.abortController?.abort();
        if (this.view) {
            this.view.webview.postMessage({ command: "clear" });
            this.send(this.view, "System", "Started a new conversation.");
        }
    }

    resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;

        view.webview.options = {
            enableScripts: true,
        };

        view.webview.html = this.getHtml();

        view.webview.onDidReceiveMessage(async message => {
            if (message.command === "newChat") {
                this.newChat();
                return;
            }

            if (message.command === "stop") {
                this.abortController?.abort();
                return;
            }

            if (message.command === "generateCode") {
                const { targetFile, operation, code, description } = message.params;
                await this.handleCodeGeneration(view, targetFile, operation, code, description);
                return;
            }

            if (message.command === "acceptChanges") {
                await this.acceptPendingChanges(view, message.params.accept);
                return;
            }

            if (message.command === "rejectChanges") {
                await this.rejectPendingChanges(view);
                return;
            }

            if (message.command === "createNewFile") {
                await this.handleNewFileCreation(view, message.params);
                return;
            }

            if (message.command === "explainCode") {
                await this.handleExplainCode(view, message.params);
                return;
            }

            if (message.command === "refactorCode") {
                await this.handleRefactorCode(view, message.params);
                return;
            }

            if (message.command === "fixError") {
                await this.handleFixError(view, message.params);
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
                const { filePath, text } = message.params;
                await this.selectTextInEditor(view, filePath, text);
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
                    this.send(
                        view,
                        "Error",
                        error instanceof Error ? error.message : String(error)
                    );
                }
            } finally {
                this.busy = false;
                this.abortController = null;
                view.webview.postMessage({ command: "busy", value: false });
                view.webview.postMessage({ command: "ready" });
            }
        });
    }

    private send(view: vscode.WebviewView, label: string, text: string): void {
        view.webview.postMessage({
            command: "response",
            label,
            text,
        });
    }

    private async runAgent(view: vscode.WebviewView, userMessage: string): Promise<void> {
        const workspaceRoot = this.getWorkspaceRoot();
        let nextMessage: string | null = userMessage;
        let toolResults: ToolResult[] = [];
        const MAX_ROUNDS = 10;

        for (let round = 0; round < MAX_ROUNDS; round++) {
            const toolCalls: AgentToolCall[] = [];
            view.webview.postMessage({ command: "assistantStart" });

            const response = await fetch(`${API_URL}/agent/stream`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    message: nextMessage,
                    context: await this.getCompactWorkspaceContext(),
                    tool_results: toolResults,
                }),
                signal: this.abortController?.signal,
            });

            if (!response.ok) {
                let detail = "Agent request failed.";
                try {
                    const data = (await response.json()) as { detail?: string };
                    if (data.detail) detail = data.detail;
                } catch {
                    // Ignore JSON parse errors.
                }
                throw new Error(detail);
            }

            const { cancelled: streamCancelled } = await this.readSSE(view, response, toolCalls);
            nextMessage = null;
            toolResults = [];

            if (streamCancelled) {
                view.webview.postMessage({ command: "assistantEnd" });
                return;
            }

            if (toolCalls.length === 0) {
                view.webview.postMessage({ command: "assistantEnd" });
                return;
            }

            for (const tool of toolCalls) {
                this.send(view, "Progress", `Working: ${tool.name.replaceAll("_", " ")}`);

                let output: string;
                try {
                    output = await this.executeTool(view, tool);
                } catch (error) {
                    output = "Operation failed: " + (error instanceof Error ? error.message : String(error));
                }

                toolResults.push({
                    tool_call_id: tool.id,
                    output,
                });
            }

            view.webview.postMessage({ command: "assistantEnd" });
        }

        this.send(view, "System", "The agent reached its maximum operation limit. Review the progress above before continuing.");
    }

    private async handleCodeGeneration(
        view: vscode.WebviewView,
        targetFile: string,
        operation: "create" | "edit" | "append" | "replace",
        code: string,
        description: string
    ): Promise<string> {
        const target = await this.resolveWorkspacePath(targetFile, true);

        let oldContent = "";
        let exists = false;

        try {
            const stat = await fs.stat(target);

            if (!stat.isFile()) {
                throw new Error("The target is not a regular file.");
            }

            if (stat.size > MAX_CONTENT_SIZE) {
                return `Changes to ${targetFile} were skipped: the existing file is too large to replace safely (>${MAX_CONTENT_SIZE} bytes).`;
            }

            oldContent = await fs.readFile(target, "utf8");
            exists = true;
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
                throw error;
            }
        }

        // Stage proposed changes in temporary files and show a native diff preview
        const storage = path.join(this.context.globalStorageUri.fsPath, "previews");
        await fs.mkdir(storage, { recursive: true });

        const id = randomUUID();
        const oldPreview = path.join(storage, `${id}.old`);
        const newPreview = path.join(storage, `${id}.new`);

        await fs.writeFile(oldPreview, oldContent, "utf8");
        await fs.writeFile(newPreview, code, "utf8");

        await vscode.commands.executeCommand(
            "vscode.diff",
            vscode.Uri.file(oldPreview),
            vscode.Uri.file(newPreview),
            `Proposed changes: ${targetFile}`
        );

        try {
            // Ask for approval
            const choice = await vscode.window.showWarningMessage(
                `${operation === "create" ? "Create" : "Modify"} ${targetFile}? Review the diff before approving.`,
                { modal: true },
                "Apply change",
                "Reject change",
                "Customize"
            );

            if (choice === "Apply change") {
                return await this.applyCodeChange(view, target, targetFile, oldContent, code);
            }

            if (choice === "Customize") {
                const customizeChoice = await vscode.window.showInputBox({
                    placeHolder: "Enter modified code or instructions...",
                    value: code,
                    prompt: `Customize the proposed changes for ${targetFile}`,
                });

                if (customizeChoice && customizeChoice.trim()) {
                    const trimmed = customizeChoice.trim();

                    await fs.writeFile(oldPreview, oldContent, "utf8");
                    await fs.writeFile(newPreview, trimmed, "utf8");

                    await vscode.commands.executeCommand(
                        "vscode.diff",
                        vscode.Uri.file(oldPreview),
                        vscode.Uri.file(newPreview),
                        `Customized changes: ${targetFile}`
                    );

                    const customizedChoice = await vscode.window.showWarningMessage(
                        `Apply customized changes for ${targetFile}?`,
                        { modal: true },
                        "Apply",
                        "Reject"
                    );

                    if (customizedChoice === "Apply") {
                        return await this.applyCodeChange(view, target, targetFile, oldContent, trimmed);
                    }
                }

                this.send(view, "System", `Changes to ${targetFile} were rejected. No file was modified.`);
                return `Changes to ${targetFile} were rejected. No file was modified.`;
            }

            if (choice === "Reject change" || choice === undefined) {
                this.send(view, "System", `Changes to ${targetFile} were rejected. No file was modified.`);
                return `Changes to ${targetFile} were rejected. No file was modified.`;
            }

            this.send(view, "System", `Changes to ${targetFile} were cancelled. No file was modified.`);
            return `Changes to ${targetFile} were cancelled. No file was modified.`;
        } finally {
            // Clean up temporary preview files (the diff view retains its own copy)
            await fs.rm(oldPreview, { force: true }).catch(() => {});
            await fs.rm(newPreview, { force: true }).catch(() => {});
        }
    }

    private async applyCodeChange(
        view: vscode.WebviewView,
        target: string,
        targetFile: string,
        oldContent: string,
        code: string
    ): Promise<string> {
        // Check for conflicts
        let latestContent = "";
        let latestExists = false;

        try {
            const stat = await fs.stat(target);

            if (!stat.isFile()) {
                throw new Error("The target is not a regular file.");
            }

            latestContent = await fs.readFile(target, "utf8");
            latestExists = true;
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
                throw error;
            }
        }

        if (latestExists && latestContent !== oldContent) {
            const conflictChoice = await vscode.window.showWarningMessage(
                `The file ${targetFile} has been modified since generation started. Apply changes anyway?`,
                { modal: true },
                "Apply anyway",
                "Cancel"
            );

            if (conflictChoice !== "Apply anyway") {
                this.send(view, "System", `Changes to ${targetFile} were rejected due to conflict.`);
                return `Changes to ${targetFile} were rejected due to conflict.`;
            }
        }

        // Apply the changes
        const parent = path.dirname(target);
        await fs.mkdir(parent, { recursive: true });
        await fs.writeFile(target, code, "utf8");

        // Open the file in VS Code
        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target));
        await vscode.window.showTextDocument(document, {
            preview: false,
        });

        this.send(view, "Progress", `Saved ${targetFile}. The file was opened in VS Code.`);
        return `Saved ${targetFile}. The file was opened in VS Code.`;
    }

    private async handleNewFileCreation(
        view: vscode.WebviewView,
        params: { targetFile: string; code: string; description: string }
    ): Promise<string> {
        if (!params?.targetFile || typeof params.code !== "string") {
            return "create_new_file requires 'targetFile' and 'code'.";
        }

        const target = await this.resolveWorkspacePath(params.targetFile, true);

        // Check if file already exists
        try {
            const stat = await fs.stat(target);
            if (stat.isFile()) {
                this.send(view, "Error", `File ${params.targetFile} already exists.`);
                return `File ${params.targetFile} already exists.`;
            }
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
                throw error;
            }
        }

        // Show approval message
        const choice = await vscode.window.showWarningMessage(
            `Create new file ${params.targetFile}?`,
            { modal: true },
            "Create",
            "Reject"
        );

        if (choice !== "Create") {
            this.send(view, "System", `File creation for ${params.targetFile} was rejected.`);
            return `File creation for ${params.targetFile} was rejected.`;
        }

        const parent = path.dirname(target);
        await fs.mkdir(parent, { recursive: true });
        await fs.writeFile(target, params.code, "utf8");

        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target));
        await vscode.window.showTextDocument(document, {
            preview: false,
        });

        this.send(view, "Progress", `Created ${params.targetFile}. The file was opened in VS Code.`);
        return `Created ${params.targetFile}. The file was opened in VS Code.`;
    }

    private async handleExplainCode(
        view: vscode.WebviewView,
        params: { filePath: string; focusRange?: string }
    ): Promise<string> {
        const content = await this.readFile({ path: params.filePath });
        return `Ask the AI to explain ${params.filePath} (${content.length} characters). Focus: ${params.focusRange || "the entire file"}. Send this request in the sidebar chat to the agent.`;
    }

    private async handleRefactorCode(
        view: vscode.WebviewView,
        params: { filePath: string; instructions: string; focusRange?: string }
    ): Promise<string> {
        const content = await this.readFile({ path: params.filePath });
        return `Ask the AI to refactor ${params.filePath} (${content.length} characters): ${params.instructions}. Send this request in the sidebar chat to the agent.`;
    }

    private async handleFixError(
        view: vscode.WebviewView,
        params: { filePath: string; errorDescription: string; currentCode?: string }
    ): Promise<string> {
        const content = await this.readFile({ path: params.filePath });
        const currentCode = params.currentCode || content.substring(0, 500);
        return `Ask the AI to fix ${params.filePath} (${content.length} characters): ${params.errorDescription}. Send this request in the sidebar chat to the agent. Current code: ${currentCode}`;
    }

    private async selectTextInEditor(
        view: vscode.WebviewView,
        filePath: string,
        text: string
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

    private async acceptPendingChanges(view: vscode.WebviewView, accept: boolean): Promise<void> {
        if (accept) {
            this.send(view, "Progress", "Changes accepted and applied.");
        } else {
            this.send(view, "Progress", "Changes rejected. No modifications were made.");
        }
    }

    private async rejectPendingChanges(view: vscode.WebviewView): Promise<void> {
        this.send(view, "Progress", "Changes rejected. No modifications were made.");
    }

    // --- Tool implementations --------------------------------------------------

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

    private async writeFile(
        view: vscode.WebviewView,
        args: { path?: string; content?: string }
    ): Promise<string> {
        const relPath = typeof args?.path === "string" ? args.path.trim() : "";
        if (!relPath) {
            throw new Error("write_file requires a 'path' argument.");
        }

        const content = typeof args?.content === "string" ? args.content : "";

        let exists = false;
        try {
            const stat = await fs.stat(await this.resolveWorkspacePath(relPath, true));
            exists = stat.isFile();
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
                throw error;
            }
        }

        return await this.handleCodeGeneration(
            view,
            relPath,
            exists ? "edit" : "create",
            content,
            `${exists ? "Update" : "Create"} ${relPath}`
        );
    }

    private async runCommand(
        view: vscode.WebviewView,
        args: { command?: string }
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
            "Cancel"
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

    // --- Live staging preview (unsaved, in-memory editor tab) ----------------

    private previewDoc: vscode.TextDocument | undefined;
    private previewEditor: vscode.TextEditor | undefined;
    private previewContent = "";

    private resetPreview(): void {
        this.previewDoc = undefined;
        this.previewEditor = undefined;
        this.previewContent = "";
    }

    private languageForFile(fileHint: string): string {
        const ext = path.extname(fileHint || "").toLowerCase();
        const map: Record<string, string> = {
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".py": "python",
            ".json": "json",
            ".md": "markdown",
            ".css": "css",
            ".scss": "scss",
            ".html": "html",
            ".xml": "xml",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".cs": "csharp",
            ".sh": "shellscript",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".sql": "sql",
        };
        return map[ext] ?? "plaintext";
    }

    private async ensurePreviewEditor(fileHint: string): Promise<void> {
        if (this.previewDoc && this.previewEditor) {
            return;
        }

        this.previewDoc = await vscode.workspace.openTextDocument({
            language: this.languageForFile(fileHint),
            content: "",
        });
        this.previewEditor = await vscode.window.showTextDocument(this.previewDoc, {
            preview: true,
        });
    }

    private async updatePreviewEditor(content: string): Promise<void> {
        if (!this.previewDoc || !this.previewEditor) {
            return;
        }

        const doc = this.previewDoc;
        this.previewContent = content;

        try {
            await this.previewEditor.edit(editBuilder => {
                const range = new vscode.Range(
                    0,
                    0,
                    doc.lineCount - 1,
                    doc.lineAt(doc.lineCount - 1).text.length
                );
                editBuilder.replace(range, content);
            });
        } catch {
            // Editor may have been closed by the user — preview is best-effort.
        }
    }

    // --- SSE / streaming ------------------------------------------------------

    private extractPartialJsonString(acc: string, key: string): string | null {
        const keyIdx = acc.indexOf(`"${key}"`);
        if (keyIdx < 0) {
            return null;
        }

        const colonIdx = acc.indexOf(":", keyIdx + key.length + 2);
        if (colonIdx < 0) {
            return null;
        }

        const quoteIdx = acc.indexOf('"', colonIdx + 1);
        if (quoteIdx < 0) {
            return null;
        }

        let raw = acc.slice(quoteIdx + 1);

        // A trailing lone backslash is an incomplete escape — drop it; it will
        // arrive with the next chunk.
        if (raw.endsWith("\\")) {
            raw = raw.slice(0, -1);
        }

        let out = "";
        for (let i = 0; i < raw.length; i++) {
            const c = raw[i];

            if (c === "\\") {
                const next = raw[i + 1];
                if (next === undefined) {
                    out += "\\";
                    return out;
                }
                if (next === "n") { out += "\n"; i++; }
                else if (next === "t") { out += "\t"; i++; }
                else if (next === "r") { out += "\r"; i++; }
                else if (next === "b") { out += "\b"; i++; }
                else if (next === "f") { out += "\f"; i++; }
                else if (next === '"') { out += '"'; i++; }
                else if (next === "\\") { out += "\\"; i++; }
                else if (next === "/") { out += "/"; i++; }
                else if (next === "u") {
                    const hex = raw.slice(i + 2, i + 6);
                    if (/^[0-9a-fA-F]{4}$/.test(hex)) {
                        out += String.fromCharCode(parseInt(hex, 16));
                        i += 5;
                    } else {
                        out += "\\u";
                        i++;
                    }
                } else {
                    out += "\\" + next;
                    i++;
                }
            } else {
                out += c;
            }
        }

        return out;
    }

    private parseSseBlock(block: string): Array<{ name: string; data: string }> {
        const events: Array<{ name: string; data: string }> = [];
        const lines = block.split(/\r?\n/);
        let current: { name: string; dataLines: string[] } | null = null;

        const flush = (): void => {
            if (current && current.dataLines.length > 0) {
                events.push({ name: current.name, data: current.dataLines.join("\n") });
            }
            current = null;
        };

        for (const rawLine of lines) {
            const line = rawLine;

            if (line.startsWith("event:")) {
                // Each "event:" line kicks off a new event group.
                flush();
                current = { name: line.slice(6).trim() || "message", dataLines: [] };
            } else if (line.startsWith("data:")) {
                if (!current) {
                    current = { name: "message", dataLines: [] };
                }
                current.dataLines.push(line.slice(5).trimStart());
            }
        }

        flush();
        return events;
    }

    private async readSSE(
        view: vscode.WebviewView,
        response: Response,
        toolCalls: AgentToolCall[]
    ): Promise<{ prose: string; cancelled: boolean }> {
        const reader = response.body?.getReader();
        if (!reader) {
            throw new Error("The API response body is not readable.");
        }

        const decoder = new TextDecoder();
        let buffer = "";
        let prose = "";
        let previewStreamIdx = -1;
        const toolAcc = new Map<number, { id: string; name: string; arguments: string }>();
        let cancelled = false;

        const post = (message: Record<string, unknown>): void => {
            view.webview.postMessage(message);
        };

        const handleEvent = async (name: string, dataStr: string): Promise<void> => {
            let data: any = null;
            try {
                data = JSON.parse(dataStr);
            } catch {
                data = null;
            }

            if (name === "delta" && data && typeof data.content === "string" && data.content) {
                prose += data.content;
                post({ command: "assistantDelta", text: data.content });
                return;
            }

            if (name === "file_change" && data && typeof data.current_file === "string") {
                post({ command: "fileChange", filePath: data.current_file });
                return;
            }

            if (name === "tool_call_delta" && data) {
                const idx = typeof data.index === "number" ? data.index : 0;
                const rawName = typeof data.name === "string" ? data.name : "";

                if (rawName !== "write_file") {
                    return;
                }

                if (previewStreamIdx !== idx) {
                    previewStreamIdx = idx;
                    this.resetPreview();
                }

                const acc = toolAcc.get(idx) ?? { id: "", name: rawName, arguments: "" };
                acc.id = typeof data.id === "string" && data.id ? data.id : acc.id;
                acc.name = rawName;
                acc.arguments += typeof data.arguments_delta === "string" ? data.arguments_delta : "";
                toolAcc.set(idx, acc);

                const pathHint = this.extractPartialJsonString(acc.arguments, "path");
                if (pathHint) {
                    if (!this.previewDoc) {
                        await this.ensurePreviewEditor(pathHint);
                    }
                    post({ command: "fileChange", filePath: pathHint });
                }

                const content = this.extractPartialJsonString(acc.arguments, "content");
                if (content) {
                    await this.ensurePreviewEditor(pathHint || "preview");
                    await this.updatePreviewEditor(content);
                }
                return;
            }

            if (name === "tool_call" && data) {
                const tId = typeof data.id === "string" ? data.id : "";
                const tName = typeof data.name === "string" ? data.name : "";
                const tArgs =
                    data.arguments && typeof data.arguments === "object" && !Array.isArray(data.arguments)
                        ? (data.arguments as Record<string, unknown>)
                        : {};

                if (tId && tName) {
                    toolCalls.push({ id: tId, name: tName, arguments: tArgs });
                }

                if (tName === "write_file") {
                    const filePath = typeof tArgs.path === "string" ? tArgs.path : "file";
                    post({ command: "toolProgress", name: "write_file", filePath });
                }
                return;
            }

            if (name === "done" && data) {
                return;
            }

            if (name === "error" && data && typeof data.message === "string") {
                throw new Error(data.message);
            }

            if (name === "cancelled") {
                cancelled = true;
                return;
            }
        };

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });

                let idx;
                while ((idx = buffer.indexOf("\n\n")) !== -1) {
                    const block = buffer.slice(0, idx);
                    buffer = buffer.slice(idx + 2);

                    for (const event of this.parseSseBlock(block)) {
                        await handleEvent(event.name, event.data);
                        if (cancelled) {
                            return { prose, cancelled: true };
                        }
                    }
                }
            }

            // Flush any trailing buffered block
            if (buffer.trim()) {
                for (const event of this.parseSseBlock(buffer)) {
                    await handleEvent(event.name, event.data);
                    if (cancelled) {
                        return { prose, cancelled: true };
                    }
                }
            }
        } finally {
            try {
                reader.cancel();
            } catch {
                // Ignore cleanup errors.
            }
        }

        return { prose, cancelled };
    }

    private async executeTool(view: vscode.WebviewView, tool: AgentToolCall): Promise<string> {
        switch (tool.name) {
            case "list_files":
                return await this.listFiles(tool.arguments as { path?: string });

            case "read_file":
                return await this.readFile(tool.arguments as { path?: string });

            case "write_file":
                return await this.writeFile(view, tool.arguments as { path?: string; content?: string });

            case "run_command":
                return await this.runCommand(view, tool.arguments as { command?: string });

            case "generate_code": {
                const args = tool.arguments as {
                    targetFile?: string;
                    operation?: "create" | "edit" | "append" | "replace";
                    code?: string;
                    description?: string;
                };
                if (!args.targetFile || typeof args.code !== "string") {
                    return "The generate_code tool requires 'targetFile' and 'code' arguments.";
                }
                return await this.handleCodeGeneration(
                    view,
                    args.targetFile,
                    args.operation ?? "edit",
                    args.code,
                    args.description ?? ""
                );
            }

            case "create_new_file":
                return await this.handleNewFileCreation(view, tool.arguments as { targetFile: string; code: string; description: string });

            case "explain_code":
                return await this.handleExplainCode(view, tool.arguments as { filePath: string; focusRange?: string });

            case "refactor_code":
                return await this.handleRefactorCode(view, tool.arguments as { filePath: string; instructions: string; focusRange?: string });

            case "fix_error":
                return await this.handleFixError(view, tool.arguments as { filePath: string; errorDescription: string; currentCode?: string });

            default:
                return `Unknown tool: ${tool.name}`;
        }
    }

    // ... existing methods preserved ...

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

    .file-preview {
        padding: 12px;
        border: 1px solid var(--vscode-widget-border);
        border-radius: 6px;
        margin: 8px 0;
        background: var(--vscode-editor-background);
        overflow-x: auto;
        white-space: pre;
        font-family: var(--vscode-editor-font-family, monospace);
        font-size: 12px;
        max-height: 300px;
        overflow-y: auto;
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
<p>AI Coding Agent - Code appears in editor, not chat</p>

<div class="panel" id="panel">
    <h3>Instructions</h3>
    <p>Enter your coding task below. The AI will generate code that appears directly in your editor.</p>

    <textarea id="input" class="command-input" placeholder="Describe what you want to build or change..." rows="3"></textarea>

    <div style="margin-top: 12px;">
        <button class="action-button" id="send">Send</button>
        <button class="action-button danger secondary" id="stop" disabled>Stop</button>
    </div>

    <p class="hint">Code will appear in your editor with diff preview for approval.</p>
</div>

<div class="panel" id="sidebar">
    <h3>Sidebar</h3>
    <div id="sidebarContent">
        <p>Waiting for instruction...</p>
    </div>
</div>

<div class="panel" id="progress">
    <div class="progress-bar">
        <div class="progress-fill" id="progressFill"></div>
    </div>
    <span id="progressText">Idle</span>
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
    liveAssistant: null
};

const input = document.getElementById('input');
const send = document.getElementById('send');
const stop = document.getElementById('stop');
const sidebarContent = document.getElementById('sidebarContent');
const progress = document.getElementById('progress');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
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
    progress.style.display = busy ? 'block' : 'none';
}

function setProgress(percent) {
    progressFill.style.width = percent + '%';
    progressText.textContent = Math.round(percent) + '%';
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

    if (data.command === 'fileChange') {
        addActivityLine('Writing: ' + data.filePath);
    }

    if (data.command === 'toolProgress') {
        setProgress(30);
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
    }
});
</script>

</body>
</html>`
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
}

export function deactivate(): void {
    // Disposables in context.subscriptions are cleaned up automatically.
}
