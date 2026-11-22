"""Mocked OpenAI provider that replays a realistic streaming write_file call."""
import asyncio
import os
import sys
import types

os.environ["AI_API_KEY"] = "test-key"
os.environ["AI_BASE_URL"] = "http://127.0.0.1:1/v1"  # never used; mocked below
os.environ["AI_MODEL"] = "test-model"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server.api as api


class FakeFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, index, id, name=None, arguments=None):
        self.index = index
        self.id = id
        self.type = "function"
        self.function = FakeFunction(name, arguments) if (name or arguments) else None


class FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class FakeChunk:
    def __init__(self, delta):
        self.choices = [FakeChoice(delta)] if delta is not None else []


class FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk

    async def aclose(self):
        pass


def build_chunks():
    content_text = "#!/usr/bin/env python3\n\"\"\"Task manager demo.\"\"\"\n\nclass TaskManager:\n    def __init__(self):\n        self.tasks = []\n\n    def add(self, task):\n        self.tasks.append(task)\n"
    path = "task_manager.py"
    args_str = "{\"path\":\"" + path + "\",\"content\":" + __import__("json").dumps(content_text) + "}"

    # The full JSON arguments, split into ~7-character fragments (raw stream).
    args_fragments = [args_str[i:i + 7] for i in range(0, len(args_str), 7)]

    chunks = [
        FakeChunk(FakeDelta(content="Here is the plan:\n\n")),
        FakeChunk(FakeDelta(tool_calls=[FakeToolCall(0, "toolu_fake_1", "write_file", "")])),
    ]
    for frag in args_fragments:
        chunks.append(FakeChunk(FakeDelta(tool_calls=[FakeToolCall(0, "", None, frag)])))
    chunks.append(FakeChunk(FakeDelta(content="\n\nNow generating...")))
    return chunks


class FakeChatCompletions:
    def __init__(self):
        self._chunks = build_chunks()

    async def create(self, **kwargs):
        return FakeAsyncStream(self._chunks)


class FakeChat:
    def __init__(self):
        self.completions = FakeChatCompletions()


class FakeClient:
    def __init__(self):
        self.chat = FakeChat()


api.client = FakeClient()
print("provider mock installed")

# Start the real FastAPI app on a test port.
import uvicorn

config = uvicorn.Config(api.app, host="127.0.0.1", port=8766, log_level="warning")
server = uvicorn.Server(config)

async def main():
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
