import * as vscode from "vscode";
import * as fs from "fs/promises";
import * as path from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import { randomUUID } from "crypto";

const execFileAsync = promisify(execFile);

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
        this._onDidChangeListener = vscode.window.onDidChangeTextEditorEditor(
            (e) => {
                this._activeEditor = e.editor;
            },
            null,
            this._onDidChangeListener.token
        );
    }

    get activeEditor(): vscode.TextEditor | undefined {
        return this._activeEditor;
    }

    dispose(): void {
        this._onDidChangeListener?.dispose();
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
            changes.push({ type: "insert", line: origIdx, text: newLine });
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

    const startLine = lines[lineIndex];
    const endLineIndex = lineIndex + 1 < lines.length ? lineIndex + 1 : lines.length;
    const endLine = lines[endLineIndex - 1];

    const startPos = new vscode.Position(lineIndex, 0);
    const endPos = new vscode.Position(endLineIndex, endLine.length);

    if (change.type === "delete") {
        return new vscode.TextEdit(new vscode.Range(startPos, endPos), "");
    }

    if (change.type === "insert") {
        const insertPos = new vscode.Position(lineIndex, 0);
        return new vscode.TextEdit(new vscode.Range(insertPos, insertPos), change.text || "");
    }

    if (change.type === "modify") {
        return new vscode.TextEdit(new vscode.Range(startPos, endPos), change.text || "");
    }

    return null;
}

// Apply workspace edits
function applyWorkspaceEdits(original: string, changes: CodeChange[]): { newContent: string; edits: vscode.TextEdit[] } {
    const edits: vscode.TextEdit[] = [];
    const sortedChanges = [...changes].sort((a, b) => b.line - a.line);

    for (const change of sortedChanges) {
        const edit = createTextEdit(change, original);
        if (edit) {
            edits.push(edit);
        }
    }

    // Build new content by applying edits in reverse order
    let newContent = original;
    // Apply from bottom to top to preserve line numbers
    const reversedEdits = [...edits].reverse();
    for (const edit of reversedEdits) {
        const range = edit.range;
        newContent = newContent.substring(0, range.start.line) + // Simplified
            newContent.substring(0, range.start.character) +
            edit.newText +
            newContent.substring(range.end.line);
    }

    return { newContent, edits };
}

class ManuCodeAIProvider implements vscode.WebviewViewProvider {
    private sessionId = randomUUID();
    private busy = false;
    private view: vscode.WebviewView | undefined;
    private abortController: AbortController | null = null;
    private editorTracker: ActiveEditorTracker | null = null;

    constructor(private readonly context: vscode.ExtensionContext) {
        this.editorTracker = new ActiveEditorTracker();
    }

    dispose(): void {
        this.editorTracker?.dispose();
    }

    resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;

        view.webview.options = {
            enableScripts: true,
        };

        view.webview.html = this.getHtml();

        view.webview.onDidReceiveMessage(async message => {
            if (message.command === "newChat") {
                this.sessionId = randomUUID();
                view.webview.postMessage({ command: "clear" });
                this.send(view, "System", "Started a new conversation.");
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
                view.webview.postMessage({
                    command: "activeEditorInfo",
                    editor: this.editorTracker.activeEditor
                        ? {
                              uri: this.editorTracker.activeEditor.document.uri.fsPath,
                              languageId: this.editorTracker.activeEditor.document.languageId,
                              selection: this.editorTracker.activeEditor.selection,
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

            const result = await this.readSSE(view, response, toolCalls);
            nextMessage = null;
            toolResults = [];

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
    ): Promise<void> {
        const target = await this.resolveWorkspacePath(targetFile, true);

        let oldContent = "";
        let exists = false;

        try {
            const stat = await fs.stat(target);

            if (!stat.isFile()) {
                throw new Error("The target is not a regular file.");
            }

            if (stat.size > MAX_CONTENT_SIZE) {
                throw new Error("Existing file is too large to replace safely.");
            }

            oldContent = await fs.readFile(target, "utf8");
            exists = true;
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
                throw error;
            }
        }

        // Generate diff
        const diff = generateDiff(oldContent, code);

        // Show diff preview using VS Code's native diff editor
        const storage = this.context.globalStorageUri.fsPath;
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

        // Clean up preview files
        await fs.rm(oldPreview, { force: true }).catch(() => {});
        await fs.rm(newPreview, { force: true }).catch(() => {});

        // Ask for approval
        const choice = await vscode.window.showWarningMessage(
            `${operation === "create" ? "Create" : "Modify"} ${targetFile}? Review the diff before approving.`,
            { modal: true },
            "Apply change",
            "Reject change",
            "Customize"
        );

        if (choice === "Apply change") {
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
                    return;
                }

                oldContent = latestContent;
                // Re-generate diff with latest content
                // (we already have the diff displayed, user confirmed anyway)
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
        } else if (choice === "Customize") {
            const customizeChoice = await vscode.window.showInputBox({
                placeHolder: "Enter modified code or instructions...",
                value: code,
                prompt: `Customize the proposed changes for ${targetFile}`,
            });

            if (customizeChoice && customizeChoice.trim()) {
                const storage = this.context.globalStorageUri.fsPath;
                const id = randomUUID();
                const oldPreview = path.join(storage, `${id}.old`);
                const newPreview = path.join(storage, `${id}.new`);

                await fs.writeFile(oldPreview, oldContent, "utf8");
                await fs.writeFile(newPreview, customizeChoice, "utf8");

                await vscode.commands.executeCommand(
                    "vscode.diff",
                    vscode.Uri.file(oldPreview),
                    vscode.Uri.file(newPreview),
                    `Customized changes: ${targetFile}`
                );

                const customizedChoice2 = await vscode.window.showWarningMessage(
                    `Apply customized changes for ${targetFile}?`,
                    { modal: true },
                    "Apply",
                    "Reject"
                );

                if (customizedChoice2 === "Apply") {
                    await fs.mkdir(parent, { recursive: true });
                    await fs.writeFile(target, customizeChoice, "utf8");

                    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target));
                    await vscode.window.showTextDocument(document, {
                        preview: false,
                    });

                    this.send(view, "Progress", `Customized changes saved to ${targetFile}.`);
                }
            }
        } else {
            this.send(view, "System", `Changes to ${targetFile} were rejected. No file was modified.`);
        }
    }

    private async handleNewFileCreation(
        view: vscode.WebviewView,
        params: { targetFile: string; code: string; description: string }
    ): Promise<void> {
        // Check if file already exists
        try {
            await fs.stat(params.targetFile);
            this.send(view, "Error", `File ${params.targetFile} already exists.`);
            return;
        } catch {
            // File doesn't exist - proceed
        }

        // Show approval message
        const choice = await vscode.window.showWarningMessage(
            `Create new file ${params.targetFile}?`,
            { modal: true },
            "Create",
            "Reject"
        );

        if (choice === "Create") {
            const parent = path.dirname(params.targetFile);
            await fs.mkdir(parent, { recursive: true });
            await fs.writeFile(params.targetFile, params.code, "utf8");

            const document = await vscode.workspace.openTextDocument(vscode.Uri.file(params.targetFile));
            await vscode.window.showTextDocument(document, {
                preview: false,
            });

            this.send(view, "Progress", `Created ${params.targetFile}. The file was opened in VS Code.`);
        } else {
            this.send(view, "System", `File creation for ${params.targetFile} was rejected.`);
        }
    }

    private async handleExplainCode(
        view: vscode.WebviewView,
        params: { filePath: string; focusRange?: string }
    ): Promise<void> {
        const content = await this.readFile({ path: params.filePath } as any);

        view.webview.postMessage({
            command: "generateCode",
            params: {
                targetFile: params.filePath,
                operation: "edit",
                code: content,
                description: `Explain this code and provide insights. Focus area: ${params.focusRange || "the entire file"}`,
            },
        });
    }

    private async handleRefactorCode(
        view: vscode.WebviewView,
        params: { filePath: string; instructions: string; focusRange?: string }
    ): Promise<void> {
        const content = await this.readFile({ path: params.filePath } as any);

        view.webview.postMessage({
            command: "generateCode",
            params: {
                targetFile: params.filePath,
                operation: "edit",
                code: "",
                description: `Refactor this code: ${params.instructions}. ${params.focusRange ? `Focus: ${params.focusRange}` : ""}`,
            },
        });
    }

    private async handleFixError(
        view: vscode.WebviewView,
        params: { filePath: string; errorDescription: string; currentCode?: string }
    ): Promise<void> {
        const content = await this.readFile({ path: params.filePath } as any);

        view.webview.postMessage({
            command: "generateCode",
            params: {
                targetFile: params.filePath,
                operation: "edit",
                code: "",
                description: `Fix this error: ${params.errorDescription}. Current code: ${params.currentCode || content.substring(0, 500)}`,
            },
        });
    }

    private async selectTextInEditor(
        view: vscode.WebviewView,
        filePath: string,
        text: string
    ): Promise<void> {
        const fullPath = this.resolveWorkspacePath(filePath, true);

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

    private async executeTool(view: vscode.WebviewView, tool: AgentToolCall): Promise<string> {
        switch (tool.name) {
            case "list_files":
                return this.listFiles(tool.arguments);

            case "read_file":
                return this.readFile(tool.arguments);

            case "write_file":
                return this.writeFile(view, tool.arguments);

            case "run_command":
                return this.runCommand(view, tool.arguments);

            case "generate_code":
                const { targetFile, operation, code, description } = tool.arguments;
                return await this.handleCodeGeneration(
                    view,
                    targetFile,
                    operation,
                    code,
                    description
                );

            case "create_new_file":
                return await this.handleNewFileCreation(view, tool.arguments);

            case "explain_code":
                return await this.handleExplainCode(view, tool.arguments);

            case "refactor_code":
                return await this.handleRefactorCode(view, tool.arguments);

            case "fix_error":
                return await this.handleFixError(view, tool.arguments);

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

    <div class="command-input" id="input" placeholder="Describe what you want to build or change..."></div>

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

<div id="codePreview" style="display: none; margin: 16px 0;">
    <h3>Code Preview</h3>
    <p>Proposed code will appear here for review before approval.</p>
    <div class="file-preview" id="previewContent"></div>
    <div style="margin-top: 8px;">
        <button class="action-button" id="approveBtn">Approve and Apply</button>
        <button class="action-button secondary" id="rejectBtn">Reject</button>
    </div>
</div>

<script nonce="${nonce}">
const vscode = acquireVsCodeApi();

const state = {
    busy: false,
    pendingChanges: null,
    currentFile: null,
    generationId: null
};

const input = document.getElementById('input');
const send = document.getElementById('send');
const stop = document.getElementById('stop');
const panel = document.getElementById('panel');
const sidebar = document.getElementById('sidebar');
const sidebarContent = document.getElementById('sidebarContent');
const progress = document.getElementById('progress');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const activityLog = document.getElementById('activityLog');
const codePreview = document.getElementById('codePreview');
const previewContent = document.getElementById('previewContent');
const approveBtn = document.getElementById('approveBtn');
const rejectBtn = document.getElementById('rejectBtn');

function scrollToBottom() {
    const containers = [sidebarContent, activityLog, previewContent];
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

function showCodePreview(code, filePath, operation) {
    codePreview.style.display = 'block';
    previewContent.textContent = 'File: ' + filePath + '\nOperation: ' + operation + '\n\n' + code;
    state.pendingChanges = { filePath, code, operation };
    approveBtn.onclick = () => applyChanges();
    rejectBtn.onclick = () => rejectChanges();
}

function hideCodePreview() {
    codePreview.style.display = 'none';
    previewContent.textContent = '';
    state.pendingChanges = null;
}

function applyChanges() {
    if (!state.pendingChanges) return;
    vscode.postMessage({
        command: 'acceptChanges',
        accept: true
    });
    hideCodePreview();
}

function rejectChanges() {
    vscode.postMessage({
        command: 'rejectChanges'
    });
    hideCodePreview();
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
        addSidebarLine('ManuCodeAI: ' + data.text, 'assistant');
        setBusy(false);
    }

    if (data.command === 'delta') {
        if (state.pendingChanges) {
            previewContent.textContent += data.text;
        }
    }

    if (data.command === 'assistantStart') {
        setBusy(true);
        addSidebarLine('ManuCodeAI is thinking...', 'dimmed');
    }

    if (data.command === 'assistantEnd') {
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
    }

    if (data.command === 'activeEditorInfo') {
        const editorInfo = data.editor;
        if (editorInfo) {
            addSidebarLine('Active editor: ' + editorInfo.uri, 'dimmed');
        }
    }

    if (data.command === 'toolProgress') {
        setProgress(30);
        addSidebarLine('Working: ' + (data.name || 'tool'), 'dimmed');
    }

    if (data.command === 'clear') {
        sidebarContent.replaceChildren();
        addSidebarLine('Starting new conversation...', '');
    }
});
</script>

</body>
</html>`