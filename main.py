
import os
import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

load_dotenv()

console = Console()

API_KEY = os.getenv("AI_API_KEY")
BASE_URL = os.getenv("AI_BASE_URL") or None
MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

if not API_KEY:
    console.print("[red]Missing AI_API_KEY in .env[/red]")
    raise SystemExit(1)

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

workspace = None
history = []

SYSTEM_PROMPT = """
You are ManuCodeAI, an advanced software engineering agent.

Your responsibilities:
- Understand and develop software projects.
- Inspect existing code before making changes.
- Write clean, maintainable code.
- Debug errors and explain solutions.
- Support Python, JavaScript, TypeScript, React,
  Next.js, HTML, CSS, SQL and other languages.
- Use available tools when appropriate.
- Never claim that you changed a file unless the
  write_file tool confirms it.
- Never claim tests passed unless you ran them.
- Explain important changes to the user.
- Ask before destructive actions.

You are working inside the user's selected workspace.
"""

def safe_path(filename):
    path = (workspace / filename).resolve()

    if path != workspace and workspace not in path.parents:
        raise ValueError("Access outside workspace is forbidden.")

    return path


def list_files(directory="."):
    folder = safe_path(directory)

    if not folder.is_dir():
        return "Directory does not exist."

    ignored = {
        ".git", ".venv", "venv", "node_modules",
        "__pycache__", ".next", "dist", "build"
    }

    results = []

    for item in folder.rglob("*"):
        if any(part in ignored for part in item.relative_to(workspace).parts):
            continue

        if item.is_file():
            results.append(str(item.relative_to(workspace)))

        if len(results) >= 250:
            break

    return "\n".join(results) or "No files found."


def read_file(filename):
    path = safe_path(filename)

    if not path.is_file():
        return "File not found."

    if path.stat().st_size > 500_000:
        return "File is too large to read."

    return path.read_text(encoding="utf-8", errors="replace")


def write_file(filename, content):
    path = safe_path(filename)

    if path.exists():
        action = "overwrite"
    else:
        action = "create"

    console.print(
        Panel(
            f"[yellow]Proposed action: {action}[/yellow]\n"
            f"File: {path.relative_to(workspace)}\n\n"
            f"{content[:2500]}",
            title="ManuCodeAI File Editor"
        )
    )

    approval = console.input(
        "[bold yellow]Approve this file change? (y/n): [/bold yellow]"
    ).strip().lower()

    if approval != "y":
        return "User rejected the file modification."

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    return f"Successfully saved {filename}"


def run_command(command):
    console.print(
        Panel(
            f"[yellow]{command}[/yellow]",
            title="Terminal Command Approval"
        )
    )

    approval = console.input(
        "[bold yellow]Run this command? (y/n): [/bold yellow]"
    ).strip().lower()

    if approval != "y":
        return "User rejected command execution."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=120
        )

        return (
            f"Exit code: {result.returncode}\n"
            f"STDOUT:\n{result.stdout[-12000:]}\n"
            f"STDERR:\n{result.stderr[-12000:]}"
        )

    except subprocess.TimeoutExpired:
        return "Command timed out after 120 seconds."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Inspect files in the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a project file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"}
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or modify a project file after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a terminal command after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }
    }
]


def execute_tool(name, arguments):
    try:
        if name == "list_files":
            return list_files(**arguments)

        if name == "read_file":
            return read_file(**arguments)

        if name == "write_file":
            return write_file(**arguments)

        if name == "run_command":
            return run_command(**arguments)

        return "Unknown tool."

    except Exception as error:
        return f"Tool error: {error}"


def ask_agent(question):
    history.append({
        "role": "user",
        "content": question
    })

    for _ in range(8):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history
            ],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.2
        )

        message = response.choices[0].message

        if message.tool_calls:
            history.append(message.model_dump(exclude_none=True))

            for call in message.tool_calls:
                name = call.function.name
                arguments = json.loads(call.function.arguments)

                console.print(
                    f"\n[cyan]Using tool: {name}[/cyan]"
                )

                result = execute_tool(name, arguments)

                history.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result)
                })

            continue

        answer = message.content or "No response."

        history.append({
            "role": "assistant",
            "content": answer
        })

        console.print(Panel(
            Markdown(answer),
            title="ManuCodeAI"
        ))

        return

    console.print("[yellow]Maximum tool-call rounds reached.[/yellow]")


def main():
    global workspace

    console.print(Panel(
        "[bold gold1]MANUCODEAI[/bold gold1]\n"
        "Your personal AI software engineering agent.",
        border_style="blue"
    ))

    while True:
        selected = console.input(
            "\n[bold cyan]Enter project folder path: [/bold cyan]"
        ).strip().strip('"')

        folder = Path(selected).expanduser().resolve()

        if folder.is_dir():
            workspace = folder
            break

        console.print("[red]Folder does not exist. Try again.[/red]")

    console.print(f"\n[green]Workspace: {workspace}[/green]")
    console.print("Type /help for commands or /exit to quit.")

    while True:
        try:
            question = console.input(
                "\n[bold green]You > [/bold green]"
            ).strip()

            if not question:
                continue

            if question == "/exit":
                break

            if question == "/help":
                console.print(
                    "/help - Show commands\n"
                    "/clear - Clear conversation\n"
                    "/exit - Exit ManuCodeAI"
                )
                continue

            if question == "/clear":
                history.clear()
                console.print("[green]Conversation cleared.[/green]")
                continue

            ask_agent(question)

        except KeyboardInterrupt:
            console.print("\n[yellow]Use /exit to quit.[/yellow]")

        except Exception as error:
            console.print(f"[red]Error: {error}[/red]")


if __name__ == "__main__":
    main()
