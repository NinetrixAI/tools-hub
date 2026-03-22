"""Agent Browser tools — headless browser automation for AI agents.

Wraps the `agent-browser` CLI (https://agent-browser.dev) as @Tool functions.
Requires: npm install -g agent-browser && agent-browser install

Commands are executed via subprocess with a 30s default timeout.
The browser session persists across calls within the same agent run.
"""

import json
import subprocess

from ninetrix import Tool


def _run(args: list[str], timeout: int = 30) -> str:
    """Run an agent-browser CLI command and return output."""
    result = subprocess.run(
        ["agent-browser"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        error = result.stderr.strip()
        return f"Error (exit {result.returncode}): {error or output}"
    return output


@Tool
def browser_open(url: str) -> str:
    """Open a URL in the headless browser.

    Args:
        url: The URL to navigate to (e.g. https://example.com).
    """
    return _run(["open", url])


@Tool
def browser_snapshot() -> str:
    """Get a snapshot of the current page with interactive element references.

    Returns a list of interactive elements (links, buttons, inputs) with
    ref IDs like @e1, @e2 that can be used with click and fill commands.
    """
    return _run(["snapshot", "-i"])


@Tool
def browser_click(selector: str) -> str:
    """Click an element on the page.

    Args:
        selector: Element ref (e.g. @e1) or CSS selector (e.g. button.submit).
    """
    return _run(["click", selector])


@Tool
def browser_fill(selector: str, text: str) -> str:
    """Fill text into an input field.

    Args:
        selector: Element ref (e.g. @e3) or CSS selector (e.g. input#search).
        text: The text to type into the input.
    """
    return _run(["fill", selector, text])


@Tool
def browser_get_text(selector: str) -> str:
    """Extract text content from an element.

    Args:
        selector: Element ref (e.g. @e1) or CSS selector (e.g. h1, .content).
    """
    return _run(["get", "text", selector])


@Tool
def browser_screenshot(filename: str = "") -> str:
    """Take a screenshot of the current page.

    Args:
        filename: Optional file path to save the screenshot. If empty, saves to a temp file.
    """
    args = ["screenshot"]
    if filename:
        args.append(filename)
    return _run(args)


@Tool
def browser_wait(condition: str) -> str:
    """Wait for a condition before continuing.

    Args:
        condition: What to wait for. Examples:
            - A CSS selector: "div.results" (waits for element to appear)
            - Milliseconds: "2000" (waits 2 seconds)
            - Network idle: "network" (waits for network to settle)
            - URL pattern: "url:*/dashboard" (waits for URL match)
    """
    return _run(["wait", condition])


@Tool
def browser_close() -> str:
    """Close the browser session."""
    return _run(["close"])
