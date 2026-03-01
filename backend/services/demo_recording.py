"""Demo recording service — fetch Browserbase recordings and normalize to action summaries.

Key feature: extracts the TEXT and ATTRIBUTES of clicked elements from the
rrweb DOM tree (FullSnapshot), so the summary says "Click 'Taco Bell'" instead
of "Click at coordinates (450, 320)".
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

BROWSERBASE_RECORDING_URL = "https://api.browserbase.com/v1/sessions/{session_id}/recording"
MAX_EVENTS_TO_STORE = 500


# ---------------------------------------------------------------------------
# Browserbase recording fetch
# ---------------------------------------------------------------------------

async def fetch_session_recording(session_id: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch rrweb-style recording for a Browserbase session.
    Returns list of events: [{ type, data, sessionId?, timestamp? }, ...].
    """
    url = BROWSERBASE_RECORDING_URL.format(session_id=session_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            url,
            headers={"X-BB-API-Key": api_key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        return data["data"] if isinstance(data["data"], list) else [data["data"]]
    return []


# ---------------------------------------------------------------------------
# rrweb DOM node map — extract text/label for every node in the snapshot
# ---------------------------------------------------------------------------

def _collect_all_text(node: dict[str, Any], max_depth: int = 10) -> str:
    """Recursively collect ALL text from a node and all its descendants.

    This is critical: a <button> might contain <span><span>Taco Bell</span></span>
    and we need to get "Taco Bell" from it.
    """
    if max_depth <= 0:
        return ""
    # Text node
    if node.get("type") == 3:
        return (node.get("textContent") or "").strip()
    # Element node — recurse into all children
    parts = []
    for child in (node.get("childNodes") or []):
        if isinstance(child, dict):
            t = _collect_all_text(child, max_depth - 1)
            if t:
                parts.append(t)
    return " ".join(parts)


def _parse_attrs(node: dict[str, Any]) -> dict[str, str]:
    """Extract attributes from an rrweb node as a flat dict."""
    raw_attrs = node.get("attributes") or {}
    if isinstance(raw_attrs, dict):
        return {str(k): str(v) for k, v in raw_attrs.items()}
    if isinstance(raw_attrs, list):
        result: dict[str, str] = {}
        for pair in raw_attrs:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                result[str(pair[0])] = str(pair[1])
        return result
    return {}


def _build_node_map(node: dict[str, Any], node_map: dict[int, dict[str, str]]) -> None:
    """Recursively walk an rrweb serialized DOM node and populate node_map.

    Each entry: { "tag", "text", "label", "attrs" }
    Stores key attributes so radio/checkbox label finders can check aria-label, role, etc.
    """
    nid = node.get("id")
    if nid is None:
        # Still recurse into children even if this node has no id
        for child in (node.get("childNodes") or []):
            if isinstance(child, dict):
                _build_node_map(child, node_map)
        return

    tag = (node.get("tagName") or "").lower()
    attrs_dict = _parse_attrs(node)

    # Collect ALL descendant text (deep), not just immediate children
    text_content = _collect_all_text(node)

    # Build a concise label for this node
    label = _build_element_label(tag, text_content, attrs_dict)

    node_map[nid] = {
        "tag": tag,
        "text": text_content[:300],
        "label": label,
        "attrs": attrs_dict,  # Keep raw attrs for radio/checkbox label resolution
    }

    # Recurse into children
    for child in (node.get("childNodes") or []):
        if isinstance(child, dict):
            _build_node_map(child, node_map)


def _build_element_label(tag: str, text: str, attrs: dict[str, str]) -> str:
    """Build a human-readable label for a DOM element.

    Priority order:
    1. aria-label / aria-description
    2. alt text (for images)
    3. title attribute
    4. data-name / data-label / data-item-name (common in apps)
    5. placeholder (for inputs)
    6. value (for inputs/buttons)
    7. name attribute (for form elements)
    8. Direct text content (deep-collected)
    9. href text (for links)
    10. data-testid / role
    11. Tag + class hint
    """
    # aria-label is the gold standard
    for aria_key in ("aria-label", "aria-description", "aria-roledescription"):
        aria = attrs.get(aria_key, "").strip()
        if aria:
            return aria

    # alt text for images
    alt = attrs.get("alt", "").strip()
    if alt:
        return alt

    # title attribute
    title = attrs.get("title", "").strip()
    if title:
        return title

    # data-* attributes commonly used for element names in web apps
    for data_key in ("data-name", "data-label", "data-item-name", "data-store-name",
                     "data-restaurant-name", "data-product-name", "data-text",
                     "data-tooltip", "data-content"):
        data_val = attrs.get(data_key, "").strip()
        if data_val:
            return data_val

    # placeholder for inputs/textareas
    placeholder = attrs.get("placeholder", "").strip()
    if placeholder and tag in ("input", "textarea", "select"):
        return f"'{placeholder}' field"

    # value for buttons/inputs
    value = attrs.get("value", "").strip()
    if value and tag in ("input", "button", "option"):
        return value

    # name attribute (common in forms)
    name = attrs.get("name", "").strip()
    if name and tag in ("input", "select", "textarea", "button"):
        # Make it human-readable: "email_address" -> "email address"
        readable = name.replace("_", " ").replace("-", " ").strip()
        if readable and len(readable) < 40:
            return f"'{readable}' field"

    # Direct text content — the most common case for buttons, links, menu items
    if text:
        # Clean up: collapse whitespace, take first meaningful chunk
        cleaned = " ".join(text.split())
        if cleaned:
            # If text is very long (e.g. whole paragraph), take first ~80 chars
            if len(cleaned) > 80:
                cleaned = cleaned[:77] + "..."
            return cleaned

    # href for links (useful when link has no text, e.g. icon-only links)
    href = attrs.get("href", "").strip()
    if href and tag == "a" and not href.startswith("#") and not href.startswith("javascript:"):
        from urllib.parse import urlparse
        try:
            parsed = urlparse(href)
            path = parsed.path.strip("/")
            if path:
                return f"link to /{path}"
        except Exception:
            pass

    # NOTE: data-testid intentionally omitted — it's a developer identifier,
    # not user-visible text (e.g. "instructionsEntrypoint", "AddToCartButtonSeoOptimization")

    # Role can be helpful
    role = attrs.get("role", "").strip()
    if role and role not in ("presentation", "none", "group"):
        return f"{role}"

    # For specific tags, return something more descriptive than just the tag
    if tag == "img":
        src = attrs.get("src", "")
        if src:
            # Try to extract filename from src
            name_part = src.split("/")[-1].split("?")[0].split(".")[0]
            if name_part and len(name_part) < 40:
                readable = name_part.replace("_", " ").replace("-", " ")
                return f"image: {readable}"
        return "image"
    if tag == "svg":
        return ""  # SVGs rarely have useful info; rely on parent walk
    if tag in ("input", "textarea"):
        input_type = attrs.get("type", "text")
        return f"{input_type} input"
    if tag == "select":
        return "dropdown"

    return ""


# ---------------------------------------------------------------------------
# Apply rrweb mutations to keep the node map current
# ---------------------------------------------------------------------------

def _apply_mutations(
    mutations: dict[str, Any],
    node_map: dict[int, dict[str, str]],
    parent_map: dict[int, int],
    children_map: dict[int, list[int]],
) -> None:
    """Process rrweb Mutation incremental snapshots to update the node map.

    Handles:
    - adds: new nodes added to the DOM (with parent/child tracking)
    - texts: text content changes
    - attributes: attribute changes
    """
    # New nodes added
    for add in (mutations.get("adds") or []):
        node = add.get("node")
        parent_id = add.get("parentId")
        if isinstance(node, dict):
            _build_node_map(node, node_map)
            _build_parent_map(node, parent_id, parent_map, children_map)

    # Text content changes
    for text_change in (mutations.get("texts") or []):
        nid = text_change.get("id")
        value = (text_change.get("value") or "").strip()
        if nid is not None and nid in node_map:
            node_map[nid]["text"] = value[:200]
            if value:
                node_map[nid]["label"] = value[:80]

    # Attribute changes
    for attr_change in (mutations.get("attributes") or []):
        nid = attr_change.get("id")
        new_attrs = attr_change.get("attributes") or {}
        if nid is not None and nid in node_map and isinstance(new_attrs, dict):
            entry = node_map[nid]
            # Update stored attrs
            stored_attrs = entry.get("attrs")
            if isinstance(stored_attrs, dict):
                for k, v in new_attrs.items():
                    if v is not None:
                        stored_attrs[str(k)] = str(v)
            # Update label if a meaningful attribute changed
            for key in ("aria-label", "alt", "title", "placeholder", "value"):
                if key in new_attrs and new_attrs[key]:
                    entry["label"] = str(new_attrs[key])[:80]
                    break


# ---------------------------------------------------------------------------
# Context gathering — collect rich surrounding context for each click
# ---------------------------------------------------------------------------

def _gather_click_context(
    node_id: int,
    node_map: dict[int, dict[str, str]],
    parent_map: dict[int, int],
    children_map: dict[int, list[int]],
) -> dict[str, str]:
    """Gather rich context around a clicked element for the LLM.

    Returns a dict with:
    - "element": the direct label of the clicked element
    - "context": text from the surrounding container (the card, button, list-item, etc.)
    - "best_label": our best guess at what the user meant to click

    Strategy: walk up the tree collecting text at each level. The first
    "meaningful container" (a, button, li, article, [role=...], etc.) gives
    us the context. Headings (h1-h6) in the container give us the name.
    """
    clicked = node_map.get(node_id, {})
    element_label = clicked.get("label", "")
    element_tag = clicked.get("tag", "")

    # Gather text from ancestors at each level, stopping at a meaningful container
    CONTAINER_TAGS = {"a", "button", "li", "article", "section", "tr", "td",
                      "label", "option", "details", "summary"}
    CONTAINER_ROLES = {"button", "link", "menuitem", "option", "tab", "listitem",
                       "row", "gridcell", "treeitem", "card"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    # Tags that are too generic to be useful containers
    SKIP_TAGS = {"body", "html", "main", "header", "footer", "nav", "form"}

    container_text = ""
    heading_text = ""
    best_label = element_label
    sibling_labels: list[str] = []

    current = node_id
    for depth in range(12):
        parent_id = parent_map.get(current)
        if parent_id is None:
            break

        parent = node_map.get(parent_id)
        if not parent:
            current = parent_id
            continue

        parent_tag = parent.get("tag", "")
        parent_text = parent.get("text", "").strip()
        parent_label = parent.get("label", "")

        if parent_tag in SKIP_TAGS:
            break

        # Check for headings among siblings at this level
        for sib_id in children_map.get(parent_id, []):
            sib = node_map.get(sib_id)
            if not sib:
                continue
            sib_tag = sib.get("tag", "")
            sib_label = sib.get("label", "")
            if sib_tag in HEADING_TAGS and sib_label:
                heading_text = sib_label
            elif sib_tag == "label" and sib_label:
                sibling_labels.append(sib_label)

        # If parent is a meaningful interactive element (button, link)
        # its text IS the label
        if parent_tag in ("button", "a"):
            if _is_meaningful_label(parent_label):
                best_label = parent_label
            elif parent_text and len(parent_text) < 120 and _is_meaningful_label(parent_text):
                best_label = parent_text[:120]
            container_text = parent_text[:200] if parent_text else ""
            break

        # Check role attribute for semantic containers
        parent_attrs = parent.get("attrs") or {}
        parent_role = parent_attrs.get("role", "")

        # If parent has a semantic container role, treat as container boundary
        if parent_role in CONTAINER_ROLES:
            container_text = parent_text[:300] if parent_text else ""
            if heading_text:
                best_label = heading_text
            elif parent_text and len(parent_text) < 120 and _is_meaningful_label(parent_text):
                best_label = parent_text
            elif _is_meaningful_label(parent_label):
                best_label = parent_label
            break

        # If we hit a card-like container with substantial text, capture it
        if parent_tag in CONTAINER_TAGS or (parent_text and len(parent_text) > 10):
            container_text = parent_text[:300] if parent_text else ""
            # If container has a heading, that's the best label
            if heading_text and _is_meaningful_label(heading_text):
                best_label = heading_text
            elif parent_label and parent_tag in CONTAINER_TAGS and _is_meaningful_label(parent_label):
                best_label = parent_label
            break

        # If parent has substantial text (like a card), use it as context
        if parent_text and len(parent_text) > 20:
            container_text = parent_text[:300]
            if heading_text:
                best_label = heading_text
            elif not best_label:
                best_label = parent_label
            break

        current = parent_id

    # If we found sibling labels (e.g. <label> next to <input>) and have no good label
    if not best_label and sibling_labels:
        best_label = sibling_labels[0]

    # If heading text found but best_label is still the raw element, prefer heading
    if heading_text and (not best_label or best_label == element_label):
        best_label = heading_text

    return {
        "element": element_label,
        "context": container_text,
        "best_label": best_label,
    }


def _build_parent_map(
    node: dict[str, Any],
    parent_id: int | None,
    parent_map: dict[int, int],
    children_map: dict[int, list[int]],
) -> None:
    """Build child→parent and parent→children mappings from the rrweb DOM tree."""
    nid = node.get("id")
    if nid is not None:
        if parent_id is not None:
            parent_map[nid] = parent_id
            children_map.setdefault(parent_id, []).append(nid)
    for child in (node.get("childNodes") or []):
        if isinstance(child, dict):
            _build_parent_map(child, nid, parent_map, children_map)


_GENERIC_LABELS = frozenset({
    "radio input", "checkbox input", "text input", "button", "image",
    "dropdown", "menuitem", "link", "option", "listitem",
    "radio", "checkbox", "input", "tab", "switch", "img",
    "icon", "svg", "presentation", "none", "separator",
})

# camelCase pattern: lowercase letter immediately followed by uppercase letter, no spaces
_CAMEL_CASE_RE = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$")


def _is_meaningful_label(label: str) -> bool:
    """Return True if the label carries real content (not a tag/type name or developer ID)."""
    if not label:
        return False
    stripped = label.strip()
    if stripped.lower() in _GENERIC_LABELS:
        return False
    # Reject camelCase developer identifiers (e.g. "instructionsEntrypoint",
    # "AddToCartButtonSeoOptimization") — these are data-testid / class names.
    if _CAMEL_CASE_RE.match(stripped):
        return False
    # Also reject PascalCase identifiers (starts with uppercase, has internal caps)
    if re.match(r"^[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+$", stripped):
        return False
    # Reject "link to /path/..." — href paths with no readable content
    if stripped.startswith("link to /"):
        return False
    # Reject "image: <hash>" and similar auto-generated image labels
    if stripped.startswith("image:") and len(stripped) < 20:
        return False
    return True


def _find_radio_option_label(
    node_id: int,
    node_map: dict[int, dict[str, str]],
    parent_map: dict[int, int],
    children_map: dict[int, list[int]],
) -> tuple[str, str]:
    """Find the label AND group name for a radio button or checkbox option.

    Returns: (option_label, group_label)

    Modern web apps structure radio options in many ways:
      1. <label> <input type="radio"/> <span>Option</span> </label>
      2. <div role="radio" aria-label="Option"> <input/> </div>
      3. <div> <input/> <div>Option text</div> <div>+$2.00</div> </div>
      4. <div role="radiogroup" aria-label="Choose size"> <div>...<div> </div>

    Strategy: walk up 1-4 levels from the <input>, at each level:
      a) Check parent's aria-label (often the best source)
      b) Collect text from sibling elements (span, div, label)
      c) Check parent's text content as last resort
    Also look for a group label (role=radiogroup, role=group, fieldset legend).
    """
    option_label = ""
    group_label = ""

    # Check the node ITSELF first — for div[role="radio"] the aria-label
    # is on the clicked element, not a parent.
    self_node = node_map.get(node_id)
    if self_node:
        self_attrs = self_node.get("attrs") or {}
        self_aria = self_attrs.get("aria-label", "").strip()
        self_role = self_attrs.get("role", "")
        self_text = self_node.get("text", "").strip()
        if self_aria and _is_meaningful_label(self_aria):
            option_label = self_aria
        elif self_role in ("radio", "option", "checkbox") and self_text and len(self_text) < 120:
            if _is_meaningful_label(self_text):
                option_label = self_text[:120]

    current = node_id
    for depth in range(10):  # Walk up to 10 levels (SPAs nest deeply)
        parent_id = parent_map.get(current)
        if parent_id is None:
            break

        parent = node_map.get(parent_id)
        if not parent:
            current = parent_id
            continue

        parent_tag = parent.get("tag", "")
        parent_label = parent.get("label", "").strip()
        parent_text = parent.get("text", "").strip()
        parent_attrs = parent.get("attrs") or {}
        parent_role = parent_attrs.get("role", "")
        parent_aria = parent_attrs.get("aria-label", "").strip()

        # If parent has aria-label, that's the gold standard for this option
        if parent_aria and not option_label:
            option_label = parent_aria

        # Check for group-level container (radiogroup, group, fieldset)
        if parent_role in ("radiogroup", "group", "listbox") or parent_tag == "fieldset":
            if parent_aria:
                group_label = parent_aria
            elif parent_tag == "fieldset":
                for child_id in children_map.get(parent_id, []):
                    child = node_map.get(child_id)
                    if child and child.get("tag") == "legend":
                        legend_text = child.get("label", "") or child.get("text", "")
                        if legend_text:
                            group_label = legend_text.strip()[:80]
                            break
            elif _is_meaningful_label(parent_label):
                group_label = parent_label
            if option_label:
                break
            current = parent_id
            continue

        # Collect text from all siblings at this level
        sibling_ids = children_map.get(parent_id, [])
        sibling_texts: list[str] = []
        for sib_id in sibling_ids:
            if sib_id == current:  # Skip the child we came from
                continue
            sib = node_map.get(sib_id)
            if not sib:
                continue
            sib_tag = sib.get("tag", "")
            sib_text = sib.get("text", "").strip()
            sib_label = sib.get("label", "").strip()

            if sib_tag in ("input", "svg", "style", "script", "path"):
                continue

            text = sib_label or sib_text
            if text and len(text) < 120 and _is_meaningful_label(text):
                sibling_texts.append(text)

        if sibling_texts and not option_label:
            option_label = " — ".join(sibling_texts)[:120]

        # Parent's own text as fallback (catches labels in cousin nodes).
        # The `text` field includes ALL descendant text from _collect_all_text.
        # Only use if reasonably short (< 120 chars = likely one option).
        if not option_label and parent_text and len(parent_text) < 120:
            if _is_meaningful_label(parent_text):
                option_label = parent_text.strip()[:120]

        # Parent with role="radio"/"option" is the option container
        if parent_role in ("radio", "option", "menuitemradio", "menuitemcheckbox"):
            if not option_label and parent_text and _is_meaningful_label(parent_text):
                option_label = parent_text[:120]
            if option_label:
                break

        # Parent is a <label> wrapping the input
        if parent_tag == "label" and parent_text and not option_label:
            if _is_meaningful_label(parent_text):
                option_label = parent_text[:120]

        # If we found option label, keep going up only for group label
        if option_label and not group_label:
            current = parent_id
            continue

        if option_label and group_label:
            break

        # Stop if parent text is too large (we've passed the option container)
        if len(parent_text) > 500:
            break

        current = parent_id

    return (option_label, group_label)


# ---------------------------------------------------------------------------
# Post-processing: correlate click→select_option pairs
# ---------------------------------------------------------------------------

def _correlate_click_select_pairs(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge click + select_option pairs that represent a single radio/checkbox selection.

    Pattern: user clicks a visible radio option element → rrweb fires a click event
    (with good label from the visible DOM) followed by an isChecked Input event
    (with poor label from the hidden <input>).

    If a click immediately precedes a select_option (within 2 actions) and the click
    has a better label, transfer the label to the select_option and remove the click.
    """
    if len(actions) < 2:
        return actions

    merged: list[dict[str, Any]] = []
    skip_next = False

    for i, action in enumerate(actions):
        if skip_next:
            skip_next = False
            continue

        # Look ahead: is the next (or next-next) action a select_option?
        if action["action"] == "click":
            click_label = action.get("best_label", "") or action.get("element", "")
            click_tag = action.get("tag", "")

            # Check next 1-2 actions for a select_option to merge with
            for lookahead in range(1, min(3, len(actions) - i)):
                next_action = actions[i + lookahead]
                if next_action["action"] == "select_option":
                    next_label = next_action.get("element", "")

                    # If click has a better label, transfer it
                    if _is_meaningful_label(click_label) and not _is_meaningful_label(next_label):
                        next_action["element"] = click_label
                        # Also transfer context if the select_option has none
                        if not next_action.get("context") and action.get("context"):
                            next_action["context"] = action["context"]

                    # Remove the click — the select_option now carries the info
                    # (skip this click action, the select_option will be added normally)
                    skip_next = (lookahead == 1)
                    break
                elif next_action["action"] not in ("click",):
                    # If there's a non-click, non-select action in between, don't merge
                    break

            if skip_next:
                continue

        merged.append(action)

    return merged


# ---------------------------------------------------------------------------
# Main normalization
# ---------------------------------------------------------------------------

def normalize_recording_to_actions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce rrweb events to a short list of high-level actions for LLM context.

    rrweb event types: 2=FullSnapshot, 3=IncrementalSnapshot, 4=Meta, 5=Custom.

    IMPORTANT: rrweb v2 (used by Browserbase) has different source numbers than v1:
      v1: 0=Mutation, 1=MouseMove, 2=MouseInteraction, 3=Scroll, 4=Input
      v2: 0=Mutation, 1=MouseMove, 2=MouseInteraction, 3=Scroll, 4=ViewportResize, 5=Input, ...

    We handle BOTH mappings to be safe.
    """
    actions: list[dict[str, Any]] = []
    urls_seen: set[str] = set()
    node_map: dict[int, dict[str, str]] = {}
    parent_map: dict[int, int] = {}
    children_map: dict[int, list[int]] = {}

    EVENT_TYPES = {"FullSnapshot": 2, "IncrementalSnapshot": 3, "Meta": 4, "Custom": 5}
    # Support both rrweb v1 (Input=4) and v2 (Input=5)
    SOURCE_MUTATION = 0
    SOURCE_MOUSE_INTERACTION = 2
    SOURCE_INPUT_V1 = 4
    SOURCE_INPUT_V2 = 5
    # rrweb MouseInteraction types: 0=MouseUp, 1=MouseDown, 2=Click, ...
    CLICK_TYPE = 2

    for evt in events:
        evt_type = evt.get("type")
        data = evt.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        # ── FullSnapshot: rebuild the node map from the DOM tree ──
        if evt_type == EVENT_TYPES["FullSnapshot"]:
            root = data.get("node")
            if isinstance(root, dict):
                node_map.clear()
                parent_map.clear()
                children_map.clear()
                _build_node_map(root, node_map)
                _build_parent_map(root, None, parent_map, children_map)
            continue

        # ── Meta: extract navigation URLs ──
        if evt_type == EVENT_TYPES["Meta"]:
            url = data.get("href") or data.get("url")
            if url and url not in urls_seen:
                urls_seen.add(url)
                actions.append({"action": "navigate", "url": url})
            continue

        # ── IncrementalSnapshot: clicks, inputs, mutations ──
        if evt_type == EVENT_TYPES["IncrementalSnapshot"]:
            source = data.get("source")

            # Mutations — keep the node map up to date
            if source == SOURCE_MUTATION:
                _apply_mutations(data, node_map, parent_map, children_map)
                continue

            # Mouse interactions — extract element label + surrounding context
            if source == SOURCE_MOUSE_INTERACTION:
                interaction_type = data.get("type")
                # Only record Click events (type=2), skip MouseUp/MouseDown
                if interaction_type != CLICK_TYPE:
                    continue

                x = data.get("x", 0)
                y = data.get("y", 0)
                node_id = data.get("id")

                # Filter phantom clicks: (0,0) coordinates on generic elements
                # These are framework artifacts, not real user interactions
                if x == 0 and y == 0:
                    continue

                # Gather rich context: the element itself + the surrounding card/container
                element_label = ""
                element_tag = ""
                context_text = ""
                best_label = ""

                if node_id is not None:
                    node_info = node_map.get(node_id)
                    if node_info:
                        element_tag = node_info.get("tag", "")

                    # DEBUG: dump the clicked node and its parent chain
                    _dbg_node = node_map.get(node_id)
                    print(f"  [DBG click] node_id={node_id} "
                          f"tag={_dbg_node.get('tag') if _dbg_node else '?'} "
                          f"label={(_dbg_node.get('label','') if _dbg_node else '?')[:60]!r} "
                          f"text={(_dbg_node.get('text','') if _dbg_node else '?')[:80]!r}")
                    _cur = node_id
                    for _d in range(5):
                        _pid = parent_map.get(_cur)
                        if _pid is None:
                            break
                        _pn = node_map.get(_pid)
                        if _pn:
                            _pa = _pn.get("attrs", {})
                            print(f"    parent[{_d}] id={_pid} "
                                  f"tag={_pn.get('tag','')} "
                                  f"role={_pa.get('role','')} "
                                  f"label={_pn.get('label','')[:60]!r} "
                                  f"text={_pn.get('text','')[:80]!r}")
                        _cur = _pid

                    ctx = _gather_click_context(
                        node_id, node_map, parent_map, children_map
                    )
                    element_label = ctx["element"]
                    context_text = ctx["context"]
                    best_label = ctx["best_label"]

                    # For clicks on radio/checkbox/label elements (real HTML
                    # or role-based custom widgets), use the specialized
                    # finder which understands form option patterns.
                    node_attrs = (node_info or {}).get("attrs", {})
                    input_type = node_attrs.get("type", "")
                    node_role = node_attrs.get("role", "")
                    is_radio_like = (
                        element_tag == "label"
                        or input_type in ("radio", "checkbox")
                        or node_role in ("radio", "option", "checkbox",
                                         "menuitemradio", "menuitemcheckbox")
                    )
                    if is_radio_like:
                        opt_lbl, grp_lbl = _find_radio_option_label(
                            node_id, node_map, parent_map, children_map
                        )
                        if _is_meaningful_label(opt_lbl):
                            best_label = opt_lbl
                            element_label = opt_lbl
                        if grp_lbl and not context_text:
                            context_text = grp_lbl

                actions.append({
                    "action": "click",
                    "x": x,
                    "y": y,
                    "element": element_label,
                    "best_label": best_label,
                    "context": context_text,
                    "tag": element_tag,
                })
                continue

            # Input events — handle BOTH rrweb v1 (source=4) and v2 (source=5)
            if source in (SOURCE_INPUT_V1, SOURCE_INPUT_V2):
                text = data.get("text", "")
                is_checked = data.get("isChecked")
                node_id = data.get("id")
                element_label = ""
                context_text = ""
                group_label = ""

                if node_id is not None:
                    # DEBUG: dump input event details
                    if is_checked is not None:
                        _dbg_in = node_map.get(node_id)
                        print(f"  [DBG input isChecked={is_checked}] node_id={node_id} "
                              f"tag={_dbg_in.get('tag') if _dbg_in else '?'} "
                              f"label={(_dbg_in.get('label','') if _dbg_in else '?')[:60]!r} "
                              f"attrs={dict(list((_dbg_in.get('attrs',{}) if _dbg_in else {}).items())[:5])}")
                        _cur2 = node_id
                        for _d2 in range(4):
                            _pid2 = parent_map.get(_cur2)
                            if _pid2 is None:
                                break
                            _pn2 = node_map.get(_pid2)
                            if _pn2:
                                print(f"    input-parent[{_d2}] id={_pid2} "
                                      f"tag={_pn2.get('tag','')} "
                                      f"role={(_pn2.get('attrs',{}) or {}).get('role','')} "
                                      f"text={_pn2.get('text','')[:80]!r}")
                            _cur2 = _pid2

                    # Filter hidden analytics/tracking inputs: if any ancestor
                    # within 3 levels is a <form>, these are likely internal
                    # tracking fields, not user-visible radio buttons.
                    # Real customization radios are in div/label/fieldset, not <form>.
                    if is_checked is not None:
                        _cur = node_id
                        _is_tracking = False
                        for _lvl in range(4):
                            _pid = parent_map.get(_cur)
                            if _pid is None:
                                break
                            _pn = node_map.get(_pid)
                            if _pn and _pn.get("tag") == "form":
                                _is_tracking = True
                                break
                            _cur = _pid
                        if _is_tracking:
                            continue  # Skip hidden tracking input

                    # For radio/checkbox: ALWAYS use the specialized label
                    # finder first — it understands the DOM patterns around
                    # inputs (sibling spans, aria-labels on parent, group labels).
                    if is_checked is not None:
                        option_lbl, grp_lbl = _find_radio_option_label(
                            node_id, node_map, parent_map, children_map
                        )
                        if option_lbl:
                            element_label = option_lbl
                        if grp_lbl:
                            group_label = grp_lbl

                    # Fall back to general context gathering if specialized
                    # finder didn't produce a meaningful label
                    if not _is_meaningful_label(element_label):
                        ctx = _gather_click_context(
                            node_id, node_map, parent_map, children_map
                        )
                        if _is_meaningful_label(ctx["best_label"]):
                            element_label = ctx["best_label"]
                        elif _is_meaningful_label(ctx["element"]):
                            element_label = ctx["element"]
                        context_text = ctx["context"]

                # For checkbox/radio changes, record them as toggle actions
                if is_checked is not None:
                    # Only record "checked" events (skip unchecked to reduce noise)
                    if is_checked:
                        actions.append({
                            "action": "select_option",
                            "element": element_label,
                            "group": group_label,
                            "context": context_text,
                        })
                    continue

                if isinstance(text, str) and len(text.strip()) > 0:
                    actions.append({
                        "action": "type",
                        "text": text[:200],
                        "element": element_label,
                    })
                continue

        # ── Custom events: navigation hints ──
        if evt_type == EVENT_TYPES["Custom"]:
            payload = data.get("payload") or data
            if isinstance(payload, dict):
                if payload.get("href"):
                    url = payload["href"]
                    if url not in urls_seen:
                        urls_seen.add(url)
                        actions.append({"action": "navigate", "url": url})
                elif payload.get("source") == "navigation":
                    url = payload.get("url")
                    if url and url not in urls_seen:
                        urls_seen.add(url)
                        actions.append({"action": "navigate", "url": url})

    # Post-process: correlate click→select_option pairs.
    # When a user clicks a radio option, we often get:
    #   click (with good label from the visible element) → select_option (with no label from the hidden input)
    # Transfer the click label to the select_option and merge them.
    actions = _correlate_click_select_pairs(actions)

    # Fallback: if no actions extracted, try to get URLs from snapshots
    if not actions and events:
        urls_from_snapshots: list[str] = []
        for evt in events[:50]:
            data = evt.get("data") or {}
            if isinstance(data, dict):
                if data.get("href"):
                    urls_from_snapshots.append(data["href"])
                node = data.get("root", {}).get("childNodes", [])
                if node and isinstance(node, list):
                    _collect_urls_from_node(node, urls_from_snapshots)
        for url in urls_from_snapshots[:20]:
            if url and url not in urls_seen:
                urls_seen.add(url)
                actions.append({"action": "navigate", "url": url})

    return actions


def _collect_urls_from_node(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("tagName") == "a":
            for prop in node.get("attributes", []) or []:
                if isinstance(prop, list) and len(prop) >= 2 and prop[0] == "href":
                    out.append(str(prop[1]))
                    break
        for child in node.get("childNodes", []) or []:
            _collect_urls_from_node(child, out)
    elif isinstance(node, list):
        for child in node:
            _collect_urls_from_node(child, out)


def _url_to_label(url: str) -> str:
    """Turn a URL into a short human-readable page label for the LLM."""
    if not url or not isinstance(url, str):
        return "a page"
    url = url.strip()
    try:
        from urllib.parse import urlparse
        p = urlparse(url if "://" in url else "https://" + url)
        host = (p.netloc or p.path or "").lower()
        path = (p.path or "").strip("/")
        if "login" in host or "login" in path or "signin" in path or "saml" in path.lower():
            return host.split(".")[-2] + " login page" if host else "login page"
        if "duo" in host or "duosecurity" in host:
            return "Duo Security / 2FA page"
        if "accounts.google" in host:
            return "Google sign-in page"
        if "mail.google" in host or "gmail" in host:
            return "Gmail"
        if host:
            parts = host.replace("www.", "").split(".")
            if len(parts) >= 2:
                name = parts[-2].replace("-", " ").title()
                return name + " (" + host + ")"
        return host or url[:60]
    except Exception:
        return url[:80]


def actions_to_summary_text(actions: list[dict[str, Any]]) -> str:
    """Turn normalized actions into a rich text summary for the LLM.

    Includes:
    - best_label: our best guess at what was clicked (e.g. "Taco Bell")
    - element: the direct text of the clicked DOM node
    - context: the full text of the surrounding card/container
    The LLM uses all three to infer the user's actual intent.
    """
    if not actions:
        return "No recorded actions."
    lines = []
    current_page = ""
    for i, a in enumerate(actions, 1):
        act = a.get("action", "unknown")
        if act == "navigate":
            url = a.get("url", "")
            current_page = _url_to_label(url)
            lines.append(f"{i}. Navigate to {current_page} | URL: {url[:120]}")
        elif act == "click":
            best = a.get("best_label", "")
            element = a.get("element", "")
            context = a.get("context", "")
            tag = a.get("tag", "")
            page_ctx = f" on {current_page}" if current_page else ""

            parts = [f"{i}. Click"]
            # Only show labels that carry real user-visible info
            display_label = best if _is_meaningful_label(best) else ""
            if not display_label:
                display_label = element if _is_meaningful_label(element) else ""
            if display_label:
                parts.append(f"'{display_label}'")
            if tag and tag not in ("div", "span"):
                parts.append(f"({tag})")
            parts.append(page_ctx)

            line = " ".join(parts).strip()

            # Add context as extra info if it's different from the label
            # This gives the LLM the full card text to understand what was selected
            if context and context != best and context != element and len(context) > 5:
                # Collapse whitespace and truncate
                ctx_short = " ".join(context.split())[:200]
                # Skip useless context (loading spinners, empty containers)
                if ctx_short.lower() not in ("icon loading", "loading", "icon", "loading..."):
                    line += f"\n   [surrounding text: {ctx_short}]"

            lines.append(line)
        elif act == "type":
            text = (a.get("text") or "")[:100]
            element = a.get("element", "")
            page_ctx = f" on {current_page}" if current_page else ""
            if element:
                lines.append(f"{i}. Type {text!r} into '{element}'{page_ctx}")
            else:
                lines.append(f"{i}. Type: {text!r}{page_ctx}")
        elif act == "select_option":
            element = a.get("element", "")
            group = a.get("group", "")
            context = a.get("context", "")
            page_ctx = f" on {current_page}" if current_page else ""

            # Filter out developer IDs from display
            if not _is_meaningful_label(element):
                element = ""
            if not _is_meaningful_label(group):
                group = ""

            if element and group:
                line = f"{i}. Select '{element}' for {group}{page_ctx}"
            elif element:
                line = f"{i}. Select option '{element}'{page_ctx}"
            elif group:
                line = f"{i}. Select an option for {group}{page_ctx}"
            else:
                line = f"{i}. Select a radio/checkbox option{page_ctx}"

            # Include surrounding context so the LLM knows what group this belongs to
            if context and context != element and context != group and len(context) > 5:
                ctx_short = " ".join(context.split())[:200]
                line += f"\n   [surrounding text: {ctx_short}]"

            lines.append(line)
        elif act == "toggle":
            element = a.get("element", "")
            state = a.get("state", "toggled")
            page_ctx = f" on {current_page}" if current_page else ""
            if element:
                lines.append(f"{i}. {state.capitalize()} '{element}'{page_ctx}")
            else:
                lines.append(f"{i}. {state.capitalize()} a checkbox/radio{page_ctx}")
        else:
            lines.append(f"{i}. {act}: {a}")
    return "\n".join(lines)
