"""DOM-based email content extraction via Playwright JS evaluation.

Replaces the expensive screenshot-based Computer Use approach with direct
DOM queries. Much cheaper and faster — no Claude API calls needed for reading.
"""

from __future__ import annotations

from playwright.async_api import Page

GMAIL_THREAD_URL = "https://mail.google.com/mail/u/0/#inbox/thread-f:{thread_id}"
GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"

# ---------------------------------------------------------------------------
# Inbox thread list extraction
# ---------------------------------------------------------------------------

_INBOX_JS = """
() => {
    const rows = document.querySelectorAll('tr.zA');
    const threads = [];
    const seen = new Set();
    for (const row of rows) {
        const threadSpan = row.querySelector('span.bqe[data-thread-id]');
        const rawId = threadSpan ? threadSpan.getAttribute('data-thread-id') : '';
        const match = rawId.match(/thread-f:(\\d+)/);
        const threadId = match ? match[1] : '';
        if (!threadId || seen.has(threadId)) continue;
        seen.add(threadId);

        const subjectEl = row.querySelector('span.bog');
        const subject = subjectEl ? subjectEl.textContent.trim() : '(no subject)';

        const senderEl = row.querySelector('span.zF');
        const sender = senderEl ? senderEl.textContent.trim() : '';

        const snippetEl = row.querySelector('span.y2');
        const snippet = snippetEl ? snippetEl.textContent.trim().replace(/^\\s*-\\s*/, '') : '';

        const dateEl = row.querySelector('span.bq3');
        const date = dateEl ? dateEl.textContent.trim() : '';

        const isUnread = row.classList.contains('zE');

        threads.push({ id: threadId, subject, sender, snippet, date, unread: isUnread });
    }
    return threads;
}
"""


async def fetch_inbox_threads(page: Page, max_results: int = 100) -> list[dict]:
    """Navigate to Gmail inbox and extract thread list via DOM."""
    current = page.url
    # Skip navigation if already on inbox
    if "#inbox" not in current:
        await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_selector("tr.zA", timeout=15000)
    await page.wait_for_timeout(800)  # Reduced from 2000ms — just need rows to render
    threads = await page.evaluate(_INBOX_JS)
    return threads[:max_results]


# ---------------------------------------------------------------------------
# Single thread content extraction
# ---------------------------------------------------------------------------

_THREAD_CONTENT_JS = """
() => {
    // Subject
    const subjectEl = document.querySelector('h2.hP') || document.querySelector('[data-thread-perm-id] h2');
    const subject = subjectEl ? subjectEl.textContent.trim() : '';

    // Collect all messages in the thread
    // Gmail wraps each message in a container. Try multiple selectors.
    const messages = [];

    // Try expanded messages first (div with class containing "adn" or message containers)
    const msgContainers = document.querySelectorAll('div.adn, div[data-message-id]');

    if (msgContainers.length > 0) {
        for (const container of msgContainers) {
            // Sender
            const senderEl = container.querySelector('span.gD, span.go, [email]');
            const senderName = senderEl ? (senderEl.getAttribute('name') || senderEl.textContent.trim()) : '';
            const senderEmail = senderEl ? (senderEl.getAttribute('email') || '') : '';
            const from = senderName ? (senderEmail ? senderName + ' <' + senderEmail + '>' : senderName) : senderEmail;

            // Date
            const dateEl = container.querySelector('span.g3, span[title]');
            const date = dateEl ? (dateEl.getAttribute('title') || dateEl.textContent.trim()) : '';

            // Body — look for the message body div
            const bodyEl = container.querySelector('div.a3s, div[dir="ltr"], div.gmail_default');
            let body = '';
            if (bodyEl) {
                body = bodyEl.innerText.trim().substring(0, 2000);
            } else {
                // Fallback: get all text content from the container
                body = container.innerText.trim().substring(0, 2000);
            }

            if (body.length > 10) {
                messages.push({ from, date, body });
            }
        }
    }

    // Fallback: try to grab the entire visible thread text
    if (messages.length === 0) {
        const threadBody = document.querySelector('div.nH div.aHU, div[role="main"]');
        if (threadBody) {
            const fullText = threadBody.innerText.trim().substring(0, 5000);
            messages.push({ from: '', date: '', body: fullText });
        }
    }

    // Get the most recent sender from the first/last message
    const latestMsg = messages.length > 0 ? messages[messages.length - 1] : null;
    const sender = latestMsg ? latestMsg.from : '';

    return {
        subject: subject || '(no subject)',
        sender,
        messages: messages.slice(-5),  // Last 5 messages
        message_count: messages.length,
    };
}
"""


async def extract_thread_content(page: Page, thread_id: str) -> dict:
    """Navigate to a Gmail thread and extract its content via DOM queries.

    Returns dict with: subject, sender, messages, message_count
    """
    thread_url = GMAIL_THREAD_URL.format(thread_id=thread_id)
    await page.goto(thread_url, wait_until="domcontentloaded", timeout=20000)
    # Wait for the message body to appear instead of a flat timeout
    try:
        await page.wait_for_selector('div.adn, div[data-message-id], div.a3s', timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(500)  # Reduced from 3000ms

    # Try to expand collapsed messages (1 click max, faster)
    try:
        expanders = await page.query_selector_all('div.ajR, span.ajP')
        for exp in expanders[:2]:
            try:
                await exp.click()
                await page.wait_for_timeout(300)
            except Exception:
                pass
    except Exception:
        pass

    data = await page.evaluate(_THREAD_CONTENT_JS)
    data["thread_id"] = thread_id
    return data


# ---------------------------------------------------------------------------
# Thread preview (lighter, for inbox display with content)
# ---------------------------------------------------------------------------

async def fetch_thread_preview(page: Page, thread_id: str) -> dict:
    """Quick preview of a thread — navigates and extracts minimal content."""
    return await extract_thread_content(page, thread_id)
