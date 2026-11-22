/**
 * Incremental parser for a streaming JSON object's string-valued fields.
 *
 * The backend forwards the raw `arguments_delta` fragments (partial JSON
 * text) of in-flight tool calls. This parser walks those fragments
 * character by character and extracts the *decoded* text of the top-level
 * "content" and "path" string fields — the only fields the preview and the
 * file pipeline care about.
 *
 * It is deliberately tolerant:
 *  - any key order
 *  - unknown keys and nested values (skipped in a depth-correct way)
 *  - raw control characters inside strings (tolerated, passed through)
 *  - incomplete strings (decoded up to the cut; never throws)
 *  - escape sequences including `\uXXXX`
 *
 * It never throws and never emits raw JSON fragments — only the decoded
 * string-value text. Malformed partial JSON is never displayed as source code.
 */

export interface ParserOutput {
    /** Decoded characters of the "content" string value in this chunk. */
    contentDelta: string;
    /** Decoded characters of the "path" string value in this chunk. */
    pathDelta: string;
}

type Mode =
    | "scaffold" // outside any string; depth tracking only
    | "key"       // inside a key string
    | "colon"     // after a key string, expecting ":"
    | "value"     // between ":" and the value's first character
    | "string"    // inside a string value (the tracked string)
    | "skip";     // inside a nested object/array value (ignored)

export class PartialArgumentsParser {
    private mode: Mode = "scaffold";
    /** Number of open structures including the root object. */
    private depth = 0;
    /** True once the root object has closed — everything is ignored. */
    private closed = false;
    /** Decoded name of the current top-level key. */
    private key = "";
    /** Name of the key whose string value is currently being decoded. */
    private valueKey = "";
    /** Nested depth inside a skipped value (non-string regions). */
    private skipNested = 0;
    private skipString = false;
    private skipEscaped = false;
    /** Pending escape continuation (shared by key/string modes). */
    private backslash = false;
    private inHex = false;
    private hexBuffer = "";

    reset(): void {
        this.mode = "scaffold";
        this.depth = 0;
        this.closed = false;
        this.key = "";
        this.valueKey = "";
        this.skipNested = 0;
        this.skipString = false;
        this.skipEscaped = false;
        this.backslash = false;
        this.inHex = false;
        this.hexBuffer = "";
    }

    private resetEscape(): void {
        this.backslash = false;
        this.inHex = false;
        this.hexBuffer = "";
    }

    /**
     * Feed a raw fragment of the partial arguments text. Returns the decoded
     * content/path deltas produced by this fragment.
     */
    feed(chunk: string): ParserOutput {
        const writer = { contentDelta: "", pathDelta: "" };
        const route = (key: string, text: string): void => {
            if (key === "content") writer.contentDelta += text;
            else if (key === "path") writer.pathDelta += text;
        };

        for (const ch of chunk) {
            if (this.closed) return writer;

            // 1. Consume a pending escape continuation first.
            if (this.inHex) {
                if (/[0-9a-fA-F]/.test(ch)) {
                    this.hexBuffer += ch;
                    if (this.hexBuffer.length === 4) {
                        const text = safeFromHex(this.hexBuffer);
                        this.inHex = false;
                        this.hexBuffer = "";
                        if (this.mode === "key") this.key += text;
                        else route(this.valueKey, text);
                        continue;
                    }
                    continue;
                }
                // Malformed hex — keep the partial raw, resume normally.
                this.inHex = false;
                const raw = "\\u" + this.hexBuffer;
                this.hexBuffer = "";
                if (this.mode === "key") this.key += raw;
                else route(this.valueKey, raw);
                // fall through with the current char
            }
            if (this.backslash) {
                this.backslash = false;
                if (ch === "u") {
                    this.inHex = true;
                    this.hexBuffer = "";
                    continue;
                }
                const decoded = decodeSimpleEscape(ch);
                if (this.mode === "key") this.key += decoded;
                else route(this.valueKey, decoded);
                continue;
            }

            // 2. Mode-specific handling.
            switch (this.mode) {
                case "scaffold": {
                    if (ch === "{" || ch === "[") this.depth++;
                    else if (ch === "}" || ch === "]") {
                        this.depth--;
                        if (this.depth <= 0) {
                            this.depth = 0;
                            this.closed = true;
                        }
                    } else if (ch === '"') {
                        this.mode = "key";
                        this.key = "";
                        this.resetEscape();
                    }
                    break;
                }

                case "key": {
                    if (ch === "\\") this.backslash = true;
                    else if (ch === '"') this.mode = "colon";
                    else this.key += ch;
                    break;
                }

                case "colon": {
                    if (ch === ":") this.mode = "value";
                    else if (ch === '"') {
                        // Malformed (key without colon) — treat as a new key.
                        this.mode = "key";
                        this.key = "";
                        this.resetEscape();
                    } else if (ch === "}" || ch === "]") {
                        if (this.depth > 0) {
                            this.depth = Math.max(0, this.depth - 1);
                            if (this.depth === 0) this.closed = true;
                        }
                    }
                    // whitespace and other characters are tolerated.
                    break;
                }

                case "value": {
                    if (ch === '"') {
                        this.mode = "string";
                        this.valueKey = this.key;
                        this.resetEscape();
                    } else if (ch === "{" || ch === "[") {
                        this.mode = "skip";
                        this.skipNested = 1;
                        this.skipString = false;
                        this.skipEscaped = false;
                    } else if (ch === "}" || ch === "]") {
                        if (this.depth > 0) {
                            this.depth = Math.max(0, this.depth - 1);
                            if (this.depth === 0) this.closed = true;
                        }
                    } else {
                        // Scalar (number/boolean/null/plain) or garbage:
                        // ignore everything up to the next top-level key.
                        this.mode = "scaffold";
                    }
                    break;
                }

                case "string": {
                    if (ch === "\\") this.backslash = true;
                    else if (ch === '"') this.mode = "scaffold";
                    else route(this.valueKey, ch);
                    break;
                }

                case "skip": {
                    if (this.skipString) {
                        if (this.skipEscaped) this.skipEscaped = false;
                        else if (ch === "\\") this.skipEscaped = true;
                        else if (ch === '"') this.skipString = false;
                    } else {
                        if (ch === '"') this.skipString = true;
                        else if (ch === "{" || ch === "[") this.skipNested++;
                        else if (ch === "}" || ch === "]") {
                            this.skipNested--;
                            if (this.skipNested <= 0) this.mode = "scaffold";
                        }
                    }
                    break;
                }
            }
        }

        return writer;
    }

    /**
     * Flush the parser at end of stream, emitting any held remainder as raw
     * text. The parser is left in a reset state afterwards.
     */
    flush(): ParserOutput {
        const raw = this.escapeRest();
        const modeAtFlush = this.mode;
        const keyAtFlush = this.valueKey;
        this.reset();

        let contentDelta = "";
        let pathDelta = "";
        if (raw && modeAtFlush === "string") {
            if (keyAtFlush === "content") contentDelta = raw;
            else if (keyAtFlush === "path") pathDelta = raw;
        }
        return { contentDelta, pathDelta };
    }

    private escapeRest(): string {
        if (this.inHex && this.hexBuffer.length > 0) return "\\u" + this.hexBuffer;
        if (this.inHex) return "\\u";
        if (this.backslash) return "\\";
        return "";
    }
}

function safeFromHex(hex: string): string {
    if (hex.length !== 4) return "";
    const code = parseInt(hex, 16);
    if (Number.isNaN(code)) return "";
    try {
        return String.fromCharCode(code);
    } catch {
        return "";
    }
}

function decodeSimpleEscape(ch: string): string {
    switch (ch) {
        case "n": return "\n";
        case "t": return "\t";
        case "r": return "\r";
        case "b": return "\b";
        case "f": return "\f";
        case '"': return '"';
        case "\\": return "\\";
        case "/": return "/";
        default: return ch;
    }
}
