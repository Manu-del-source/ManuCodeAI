/** Build synthetic SSE streams for testing the runner, chunking/timing
 * can be controlled so deltas arrive in tiny, awkward fragments. */

export interface SseEventSpec {
    name: string;
    data: unknown;
}

export function buildSseText(events: SseEventSpec[]): string {
    return events
        .map(({ name, data }) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
        .join("");
}

export function streamFromText(
    text: string,
    chunkSize = Infinity,
    dropWithError?: Error,
    dropAfterChunks = Infinity,
): ReadableStream<Uint8Array> {
    const bytes = new TextEncoder().encode(text);
    let i = 0;
    let pulled = 0;
    return new ReadableStream({
        pull(controller) {
            if (dropWithError && pulled >= dropAfterChunks) {
                controller.error(dropWithError);
                return;
            }
            if (i >= bytes.length) {
                controller.close();
                return;
            }
            const n = Math.min(chunkSize, bytes.length - i);
            controller.enqueue(bytes.slice(i, i + n));
            i += n;
            pulled++;
        },
    });
}

export function streamWithChunkSize(text: string, chunkBytes: number): ReadableStream<Uint8Array> {
    return streamFromText(text, chunkBytes);
}

/** The raw arguments fragments as a real provider would send them:
 * the whole partial JSON, split at arbitrary points. */
export function argumentsFragments(args: string, fragmentSize = 7): string[] {
    const parts: string[] = [];
    for (let i = 0; i < args.length; i += fragmentSize) {
        parts.push(args.slice(i, i + fragmentSize));
    }
    return parts;
}
