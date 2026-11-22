/** Pure file-path → VS Code language id mapping. */

import * as path from "path";

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
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
    ".txt": "plaintext",
};

export function languageForFile(fileHint: string): string {
    const ext = path.extname(fileHint || "").toLowerCase();
    return LANGUAGE_BY_EXTENSION[ext] ?? "plaintext";
}
