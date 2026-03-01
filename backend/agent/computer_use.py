"""Claude Computer Use agent loop.

Sends screenshots to Claude and executes the mouse/keyboard actions it
requests, creating an autonomous agent that can interact with any web page
visually — no DOM selectors needed.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import anthropic
from playwright.async_api import Page

from backend.config import ANTHROPIC_API_KEY

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 900
MODEL = "claude-sonnet-4-20250514"
TOOL_VERSION = "computer_20250124"
BETA_FLAG = "computer-use-2025-01-24"
MAX_AGENT_ITERATIONS = 25  # safety cap

COMPUTER_TOOL = {
    "type": TOOL_VERSION,
    "name": "computer",
    "display_width_px": DISPLAY_WIDTH,
    "display_height_px": DISPLAY_HEIGHT,
}

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Playwright action executors
# ---------------------------------------------------------------------------


async def _take_screenshot(page: Page) -> str:
    """Capture screenshot as base64 PNG."""
    buf = await page.screenshot(type="png")
    return base64.standard_b64encode(buf).decode("utf-8")


async def _execute_action(page: Page, action: dict[str, Any]) -> str | None:
    """Execute a single computer use action on the Playwright page.
    Returns an error string if something goes wrong, else None.
    """
    act = action.get("action", "")

    try:
        if act == "screenshot":
            # Just return — the loop will capture the screenshot
            return None

        elif act == "left_click":
            coord = action.get("coordinate", [0, 0])
            modifier = action.get("text", "")
            if modifier:
                await page.keyboard.down(_map_modifier(modifier))
            await page.mouse.click(coord[0], coord[1])
            if modifier:
                await page.keyboard.up(_map_modifier(modifier))
            await page.wait_for_timeout(500)

        elif act == "right_click":
            coord = action.get("coordinate", [0, 0])
            await page.mouse.click(coord[0], coord[1], button="right")
            await page.wait_for_timeout(300)

        elif act == "double_click":
            coord = action.get("coordinate", [0, 0])
            await page.mouse.dblclick(coord[0], coord[1])
            await page.wait_for_timeout(300)

        elif act == "triple_click":
            coord = action.get("coordinate", [0, 0])
            await page.mouse.click(coord[0], coord[1], click_count=3)
            await page.wait_for_timeout(300)

        elif act == "type":
            text = action.get("text", "")
            await page.keyboard.type(text, delay=10)
            await page.wait_for_timeout(200)

        elif act == "key":
            key = action.get("text", "")
            # Map common key names to Playwright format
            mapped = _map_key_combo(key)
            await page.keyboard.press(mapped)
            await page.wait_for_timeout(200)

        elif act == "mouse_move":
            coord = action.get("coordinate", [0, 0])
            await page.mouse.move(coord[0], coord[1])

        elif act == "scroll":
            coord = action.get("coordinate", [640, 450])
            direction = action.get("scroll_direction", "down")
            amount = action.get("scroll_amount", 3)
            delta_x, delta_y = 0, 0
            if direction == "down":
                delta_y = amount * 100
            elif direction == "up":
                delta_y = -amount * 100
            elif direction == "right":
                delta_x = amount * 100
            elif direction == "left":
                delta_x = -amount * 100
            await page.mouse.wheel(delta_x, delta_y)
            await page.wait_for_timeout(300)

        elif act == "wait":
            await page.wait_for_timeout(1000)

        elif act == "left_click_drag":
            start = action.get("start_coordinate", action.get("coordinate", [0, 0]))
            end = action.get("coordinate", [0, 0])
            await page.mouse.move(start[0], start[1])
            await page.mouse.down()
            await page.mouse.move(end[0], end[1])
            await page.mouse.up()
            await page.wait_for_timeout(300)

        else:
            return f"Unknown action: {act}"

    except Exception as exc:
        return f"Action error ({act}): {type(exc).__name__}: {exc}"

    return None


def _map_modifier(mod: str) -> str:
    """Map modifier names to Playwright key names."""
    mapping = {"shift": "Shift", "ctrl": "Control", "alt": "Alt", "super": "Meta"}
    return mapping.get(mod.lower(), mod)


def _map_key_combo(key: str) -> str:
    """Map key combo strings like 'ctrl+a' to Playwright format 'Control+a'."""
    parts = key.split("+")
    mapped = []
    for p in parts:
        p_stripped = p.strip()
        lower = p_stripped.lower()
        if lower == "ctrl":
            mapped.append("Control")
        elif lower == "cmd" or lower == "super" or lower == "meta":
            mapped.append("Meta")
        elif lower == "alt":
            mapped.append("Alt")
        elif lower == "shift":
            mapped.append("Shift")
        elif lower == "return" or lower == "enter":
            mapped.append("Enter")
        elif lower == "space":
            mapped.append(" ")
        elif lower == "backspace" or lower == "delete":
            mapped.append("Backspace")
        elif lower == "escape" or lower == "esc":
            mapped.append("Escape")
        elif lower == "tab":
            mapped.append("Tab")
        else:
            mapped.append(p_stripped)
    return "+".join(mapped)


# ---------------------------------------------------------------------------
# Agent loop — the core computer use cycle
# ---------------------------------------------------------------------------


async def run_computer_use(
    page: Page,
    instruction: str,
    *,
    system_prompt: str = "",
    max_iterations: int = MAX_AGENT_ITERATIONS,
    on_screenshot: Any | None = None,  # async callback(screenshot_b64)
) -> str:
    """Run a Claude computer use agent loop on a Playwright page.

    Args:
        page: The Playwright page to control.
        instruction: What the agent should do.
        system_prompt: Optional system-level context.
        max_iterations: Safety limit on back-and-forth cycles.
        on_screenshot: Optional async callback called with each screenshot base64.

    Returns:
        The final text response from Claude.
    """
    client = _get_client()

    # Take initial screenshot
    initial_ss = await _take_screenshot(page)
    if on_screenshot:
        await on_screenshot(initial_ss)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": initial_ss,
                    },
                },
                {"type": "text", "text": instruction},
            ],
        }
    ]

    default_system = (
        "You are a computer use agent controlling a web browser. "
        "You can see the screen and interact with it using mouse and keyboard. "
        "After each action, carefully check the result before proceeding. "
        "Be efficient — complete the task with minimal actions. "
        "When the task is complete, respond with your final answer as text."
    )
    sys = system_prompt or default_system

    for iteration in range(max_iterations):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=sys,
            tools=[COMPUTER_TOOL],
            messages=messages,
            betas=[BETA_FLAG],
        )

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        # Process tool calls
        tool_results = []
        has_tool_use = False

        for block in response.content:
            if block.type == "tool_use":
                has_tool_use = True
                action_input = block.input

                if action_input.get("action") == "screenshot":
                    # Just take a screenshot and return it
                    ss = await _take_screenshot(page)
                    if on_screenshot:
                        await on_screenshot(ss)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": ss,
                                },
                            }
                        ],
                    })
                else:
                    # Execute the action
                    error = await _execute_action(page, action_input)

                    # Take screenshot after action
                    ss = await _take_screenshot(page)
                    if on_screenshot:
                        await on_screenshot(ss)

                    if error:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": error,
                            "is_error": True,
                        })
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": ss,
                                    },
                                }
                            ],
                        })

        # If no tools were used, Claude is done — extract final text
        if not has_tool_use:
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            return final_text

        # Continue the loop with tool results
        messages.append({"role": "user", "content": tool_results})

    # Safety: max iterations reached
    return "[Agent loop reached maximum iterations]"


# ---------------------------------------------------------------------------
# High-level task helpers (pre-built instructions for common Gmail tasks)
# ---------------------------------------------------------------------------


async def read_email_thread(page: Page, thread_url: str, on_screenshot=None) -> dict:
    """Navigate to a Gmail thread and extract its content using Claude vision."""
    await page.goto(thread_url, wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(3000)

    result_text = await run_computer_use(
        page,
        instruction=(
            "You are looking at a Gmail email thread. Please extract the following "
            "information and return it as a JSON object:\n"
            '- "subject": the email subject line\n'
            '- "sender": who sent the most recent email (name and/or email)\n'
            '- "messages": an array of the last 3-5 messages, each with "from", "body" (first 500 chars), and "date"\n'
            '- "message_count": total number of messages in the thread\n\n'
            "If the page hasn't fully loaded, scroll down or wait. "
            "Return ONLY the JSON object, no other text."
        ),
        system_prompt=(
            "You are an email reading agent. Your job is to look at the Gmail thread on screen "
            "and extract structured data. Use the screenshot action if you need to see the current "
            "state of the screen. Scroll down if needed to see all messages. "
            "When done, return a valid JSON object with the email data."
        ),
        on_screenshot=on_screenshot,
        max_iterations=8,
    )

    # Parse the JSON from Claude's response
    try:
        # Try to extract JSON from the response
        text = result_text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        # Fallback: return raw text as body
        return {
            "subject": "(extracted via vision)",
            "sender": "",
            "messages": [{"from": "", "body": result_text[:1000], "date": ""}],
            "message_count": 1,
        }


async def compose_draft_reply(page: Page, draft_text: str, on_screenshot=None) -> bool:
    """Click reply and type a draft in the currently open Gmail thread."""
    result = await run_computer_use(
        page,
        instruction=(
            f"In the Gmail thread currently shown on screen:\n"
            f"1. Click the Reply button (or reply area at the bottom of the thread)\n"
            f"2. Wait for the compose box to appear\n"
            f"3. Type the following draft reply:\n\n"
            f"{draft_text}\n\n"
            f"4. Do NOT click Send. Just leave the draft in the compose box — Gmail will auto-save it.\n"
            f"5. When done, confirm by saying 'Draft composed successfully'."
        ),
        system_prompt=(
            "You are an email drafting agent. Your job is to compose a reply draft in Gmail. "
            "Click Reply, wait for the compose area, and type the draft text. "
            "Do NOT send the email. Gmail auto-saves drafts. "
            "After typing, verify the text looks correct in the compose box."
        ),
        on_screenshot=on_screenshot,
        max_iterations=12,
    )
    return "success" in result.lower() or "composed" in result.lower() or "typed" in result.lower()


async def apply_label(page: Page, label_name: str, on_screenshot=None) -> bool:
    """Apply a label to the currently open Gmail thread."""
    result = await run_computer_use(
        page,
        instruction=(
            f"In the Gmail thread currently shown on screen:\n"
            f"1. Click the Labels button (tag/label icon in the toolbar above the thread)\n"
            f'2. In the label dropdown, search for or find "{label_name}"\n'
            f"3. If the label doesn't exist, create it\n"
            f"4. Apply the label and close the dropdown\n"
            f"5. Confirm by saying 'Label applied successfully'"
        ),
        system_prompt=(
            "You are a Gmail automation agent. Apply a label to the current email thread. "
            "Use keyboard shortcuts if the UI is tricky — you can press 'l' to open the label menu in Gmail."
        ),
        on_screenshot=on_screenshot,
        max_iterations=10,
    )
    return "success" in result.lower() or "applied" in result.lower()
