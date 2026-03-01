#!/usr/bin/env python3
"""Parallel Browser — A simplistic but fully functional tabbed web browser.

Built with PyQt6 and QtWebEngine (Chromium-based).

Features
--------
- Full HTML / CSS / JS rendering via Chromium
- Tab management: open, close, switch, drag-reorder
- 3×3 grid overview of all open tabs ("Parallel View")
- Standard navigation: back, forward, reload, stop
- URL bar with search fallback (Google)
- Keyboard shortcuts (Cmd/Ctrl + T/W/G/L/R, Ctrl+Tab, Escape, …)
- target=_blank / window.open() opens in a new tab
- Fullscreen video support
- Smooth animations & transitions throughout
"""

from __future__ import annotations

import os
import sys

# Suppress noisy Chromium stderr warnings (Skia Graphite backend, etc.)
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-features=SkiaGraphite --disable-gpu-shader-disk-cache",
)
os.environ.setdefault(
    "QT_LOGGING_RULES",
    "*.debug=false;qt.webenginecontext.debug=false",
)

# Redirect Chromium's native stderr chatter (Skia, etc.) on platforms where
# the env flags alone aren't enough.
if sys.platform == "darwin":
    import io as _io
    # Keep a reference to real stderr for our own messages
    _real_stderr = sys.stderr
    try:
        _devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_devnull_fd, 2)  # redirect fd 2 (C-level stderr)
        os.close(_devnull_fd)
        sys.stderr = _real_stderr  # keep Python-level stderr working
    except OSError:
        pass

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    QTimer,
    QUrl,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANIM_DURATION_MS = 220  # base animation duration
GRID_STAGGER_MS = 35  # stagger delay per grid card

NEW_TAB_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>New Tab</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    background:#1e1e2e;color:#cdd6f4;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
    display:flex;flex-direction:column;justify-content:center;align-items:center;
    height:100vh;gap:14px;
  }
  h1{font-size:42px;font-weight:300;color:#89b4fa;letter-spacing:6px;
     opacity:0;animation:fadeUp .7s cubic-bezier(.16,1,.3,1) forwards}
  p{font-size:14px;color:#585b70;
    opacity:0;animation:fadeUp .7s cubic-bezier(.16,1,.3,1) .15s forwards}
  @keyframes fadeUp{
    from{opacity:0;transform:translateY(18px)}
    to{opacity:1;transform:translateY(0)}
  }
</style></head>
<body>
  <h1>parallel</h1>
  <p>Enter a URL above to get started</p>
</body></html>
"""

SEARCH_URL = "https://www.google.com/search?q={}"

# JS console messages containing any of these are silently dropped.
_SUPPRESSED_JS_PATTERNS: set[str] = {
    "Unrecognized feature: 'web-share'",
    "Unrecognized feature: 'interest-cohort'",
    "Unrecognized feature: 'browsing-topics'",
}

# ---------------------------------------------------------------------------
# Fade overlay — draws a coloured rectangle that can be animated from opaque
# to transparent, creating a smooth "reveal" when switching content.
# ---------------------------------------------------------------------------


class _FadeOverlay(QWidget):
    """Paintable overlay for smooth cross-fade transitions."""

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = color
        self._opacity = 0.0
        self.hide()

    # -- animated property ---------------------------------------------------

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, val: float) -> None:
        self._opacity = val
        self.update()
        if val < 0.01:
            self.hide()

    fade = pyqtProperty(float, _get_opacity, _set_opacity)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._opacity > 0.005:
            p = QPainter(self)
            c = QColor(self._color)
            c.setAlphaF(self._opacity)
            p.fillRect(self.rect(), c)
            p.end()

    # -- convenience ---------------------------------------------------------

    def flash(self, duration_ms: int = ANIM_DURATION_MS) -> None:
        """Start fully opaque, then fade to transparent."""
        self.setGeometry(self.parentWidget().rect())
        self._opacity = 1.0
        self.show()
        self.raise_()

        anim = QPropertyAnimation(self, b"fade", self)
        anim.setDuration(duration_ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# ---------------------------------------------------------------------------
# Custom QWebEnginePage — routes new-window requests into our tab system
# and suppresses known noisy console warnings.
# ---------------------------------------------------------------------------


class _ParallelPage(QWebEnginePage):
    """Intercepts *target=_blank* / *window.open()* and opens a tab.
    Also silences known harmless JS console messages."""

    def __init__(self, profile: QWebEngineProfile, parent: QWebEngineView) -> None:
        super().__init__(profile, parent)
        self._browser: BrowserWindow | None = None

    def set_browser(self, browser: BrowserWindow) -> None:
        self._browser = browser

    # Qt virtual — engine wants a new window
    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage | None:  # noqa: N802
        if self._browser is not None:
            view = self._browser.add_tab()
            return view.page()
        return None

    # Qt virtual — JS console message
    def javaScriptConsoleMessage(self, level, message, line, source_id) -> None:  # noqa: N802
        for pat in _SUPPRESSED_JS_PATTERNS:
            if pat in message:
                return  # swallow silently
        super().javaScriptConsoleMessage(level, message, line, source_id)


# ---------------------------------------------------------------------------
# Main browser window
# ---------------------------------------------------------------------------


class BrowserWindow(QMainWindow):
    """Top-level window with navigation bar, tab bar, stacked web views,
    3×3 grid overview ("Parallel View"), and a status bar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Parallel")
        self.setMinimumSize(900, 600)
        self.resize(1400, 900)

        # Internal state
        self._tabs: list[QWebEngineView] = []
        self._titles: list[str] = []
        self._is_grid: bool = False
        self._keep_alive: list = []  # prevent GC of running animations

        # Shared Chromium profile
        self._profile = QWebEngineProfile.defaultProfile()
        self._configure_profile()

        # Build UI pieces
        self._build_ui()
        self._bind_shortcuts()
        self._apply_theme()

        # First tab
        self.add_tab()

    # ── Profile configuration ──────────────────────────────────────────

    def _configure_profile(self) -> None:
        s = self._profile.settings()
        assert s is not None
        on = QWebEngineSettings.WebAttribute
        s.setAttribute(on.JavascriptEnabled, True)
        s.setAttribute(on.LocalStorageEnabled, True)
        s.setAttribute(on.PluginsEnabled, True)
        s.setAttribute(on.ScrollAnimatorEnabled, True)
        s.setAttribute(on.FullScreenSupportEnabled, True)

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # --- Navigation bar ---
        nav = QWidget(objectName="nav")
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(10, 7, 10, 7)
        nav_lay.setSpacing(5)

        self._btn_back = self._nav_btn("◀", "Back (Alt+←)", self._go_back)
        self._btn_fwd = self._nav_btn("▶", "Forward (Alt+→)", self._go_forward)
        self._btn_reload = self._nav_btn("⟳", "Reload (⌘R / F5)", self._reload)
        self._btn_newtab = self._nav_btn("+", "New Tab (⌘T)", lambda: self.add_tab())
        self._btn_grid = self._nav_btn("⊞", "Parallel View (⌘G)", self._toggle_grid)
        self._btn_grid.setCheckable(True)

        self._url_bar = QLineEdit(
            objectName="urlBar",
            placeholderText="Enter a URL or search…",
        )
        self._url_bar.returnPressed.connect(self._navigate)

        btn_go = self._nav_btn("→", "Go", self._navigate, width=38)

        for w in (self._btn_back, self._btn_fwd, self._btn_reload):
            nav_lay.addWidget(w)
        nav_lay.addWidget(self._url_bar, 1)
        nav_lay.addWidget(btn_go)
        nav_lay.addWidget(self._btn_newtab)
        nav_lay.addWidget(self._btn_grid)

        root_lay.addWidget(nav)

        # --- Tab bar ---
        self._tab_bar = QTabBar(objectName="tabBar")
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setMovable(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.currentChanged.connect(self._on_tab_switched)
        self._tab_bar.tabCloseRequested.connect(self.close_tab)
        self._tab_bar.tabMoved.connect(self._on_tab_moved)

        root_lay.addWidget(self._tab_bar)

        # --- Content wrapper (holds stacked views + grid scroll) ---
        self._content_wrapper = QWidget()
        content_lay = QVBoxLayout(self._content_wrapper)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self._stack = QStackedWidget()  # one web view per tab

        self._grid_scroll = QScrollArea(widgetResizable=True, objectName="gridScroll")
        self._grid_container = QWidget()
        self._grid_lay = QGridLayout(self._grid_container)
        self._grid_lay.setSpacing(14)
        self._grid_lay.setContentsMargins(14, 14, 14, 14)
        self._grid_scroll.setWidget(self._grid_container)

        self._content = QStackedWidget()
        self._content.addWidget(self._stack)        # 0 = normal
        self._content.addWidget(self._grid_scroll)  # 1 = grid

        content_lay.addWidget(self._content, 1)

        # Fade overlay sits on top of the content area
        self._fade = _FadeOverlay(QColor(30, 30, 46), self._content_wrapper)

        root_lay.addWidget(self._content_wrapper, 1)

        # --- Status bar + progress ---
        status = QStatusBar()
        self.setStatusBar(status)
        self._progress = QProgressBar(maximumWidth=220, maximumHeight=12)
        self._progress.setTextVisible(False)
        self._progress.hide()
        status.addPermanentWidget(self._progress)

    @staticmethod
    def _nav_btn(label: str, tooltip: str, slot, *, width: int = 34) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedSize(width, 30)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    # ── Keyboard shortcuts ─────────────────────────────────────────────

    def _bind_shortcuts(self) -> None:
        bindings: list[tuple[str, object]] = [
            ("Ctrl+T", lambda: self.add_tab()),
            ("Ctrl+W", lambda: self.close_tab(self._tab_bar.currentIndex())),
            ("Ctrl+L", lambda: (self._url_bar.setFocus(), self._url_bar.selectAll())),
            ("Ctrl+G", self._toggle_grid),
            ("Ctrl+Tab", self._next_tab),
            ("Ctrl+Shift+Tab", self._prev_tab),
            ("Alt+Left", self._go_back),
            ("Alt+Right", self._go_forward),
            ("F5", self._reload),
            ("Ctrl+R", self._reload),
            ("Escape", self._on_escape),
        ]
        for seq, fn in bindings:
            action = QAction(self)
            action.setShortcut(QKeySequence(seq))
            action.triggered.connect(fn)
            self.addAction(action)

    # ── Tab management ─────────────────────────────────────────────────

    def add_tab(self, url: str | QUrl | None = None) -> QWebEngineView:
        """Create a new tab, optionally navigating to *url*.  Returns the view."""
        view = QWebEngineView()
        page = _ParallelPage(self._profile, view)
        page.set_browser(self)
        page.fullScreenRequested.connect(self._on_fullscreen)
        view.setPage(page)

        # Wire signals
        view.titleChanged.connect(lambda t, v=view: self._on_title_changed(v, t))
        view.urlChanged.connect(lambda u, v=view: self._on_url_changed(v, u))
        view.loadProgress.connect(self._on_load_progress)
        view.loadFinished.connect(self._on_load_finished)

        self._tabs.append(view)
        self._titles.append("New Tab")
        self._stack.addWidget(view)

        idx = self._tab_bar.addTab("New Tab")
        self._tab_bar.setCurrentIndex(idx)

        if url is None:
            view.setHtml(NEW_TAB_HTML, QUrl("parallel://newtab"))
        else:
            if isinstance(url, str):
                url = (
                    QUrl(url)
                    if url.startswith(("http://", "https://"))
                    else QUrl("https://" + url)
                )
            view.load(url)

        self._url_bar.setFocus()
        self._url_bar.selectAll()
        return view

    def close_tab(self, index: int) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        if len(self._tabs) == 1:
            # Last tab — reset to new-tab page instead of closing the window
            self._tabs[0].setHtml(NEW_TAB_HTML, QUrl("parallel://newtab"))
            self._titles[0] = "New Tab"
            self._tab_bar.setTabText(0, "New Tab")
            self.setWindowTitle("Parallel")
            self._url_bar.clear()
            return

        view = self._tabs.pop(index)
        self._titles.pop(index)
        self._stack.removeWidget(view)
        self._tab_bar.removeTab(index)
        view.deleteLater()

        if self._is_grid:
            self._rebuild_grid()

    def _on_tab_switched(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self._stack.setCurrentWidget(self._tabs[index])
            self._sync_url_bar(self._tabs[index])
            self._sync_nav_buttons()
            self.setWindowTitle(f"{self._titles[index]} — Parallel")
            # Smooth reveal
            self._fade.flash(ANIM_DURATION_MS)

    def _on_tab_moved(self, from_idx: int, to_idx: int) -> None:
        """Keep internal lists in sync when the user drags tabs around."""
        self._tabs.insert(to_idx, self._tabs.pop(from_idx))
        self._titles.insert(to_idx, self._titles.pop(from_idx))

    def _next_tab(self) -> None:
        n = self._tab_bar.count()
        if n > 1:
            self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() + 1) % n)

    def _prev_tab(self) -> None:
        n = self._tab_bar.count()
        if n > 1:
            self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() - 1) % n)

    # ── Navigation ─────────────────────────────────────────────────────

    def _navigate(self) -> None:
        text = self._url_bar.text().strip()
        if not text:
            return
        if "." in text and " " not in text:
            if not text.startswith(("http://", "https://")):
                text = "https://" + text
            url = QUrl(text)
        else:
            url = QUrl(SEARCH_URL.format(text))

        idx = self._tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].load(url)

    def _go_back(self) -> None:
        idx = self._tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].back()

    def _go_forward(self) -> None:
        idx = self._tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].forward()

    def _reload(self) -> None:
        idx = self._tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].reload()

    def _on_escape(self) -> None:
        if self._is_grid:
            self._toggle_grid()
        else:
            idx = self._tab_bar.currentIndex()
            if 0 <= idx < len(self._tabs):
                self._tabs[idx].stop()

    # ── Fullscreen support ─────────────────────────────────────────────

    def _on_fullscreen(self, request) -> None:
        request.accept()
        if request.toggleOn():
            self.showFullScreen()
        else:
            self.showNormal()

    # ── Signal handlers ────────────────────────────────────────────────

    def _on_title_changed(self, view: QWebEngineView, title: str) -> None:
        try:
            idx = self._tabs.index(view)
        except ValueError:
            return
        self._titles[idx] = title
        short = (title[:26] + "…") if len(title) > 26 else title
        self._tab_bar.setTabText(idx, short)
        if idx == self._tab_bar.currentIndex():
            self.setWindowTitle(f"{title} — Parallel")

    def _on_url_changed(self, view: QWebEngineView, _url: QUrl) -> None:
        try:
            idx = self._tabs.index(view)
        except ValueError:
            return
        if idx == self._tab_bar.currentIndex():
            self._sync_url_bar(view)
            self._sync_nav_buttons()

    def _on_load_progress(self, pct: int) -> None:
        if not self._progress.isVisible():
            self._progress.show()
        self._progress.setValue(pct)

    def _on_load_finished(self, ok: bool) -> None:
        # Animate progress to 100% then hide
        self._progress.setValue(100)
        QTimer.singleShot(350, self._progress.hide)
        if not ok:
            bar = self.statusBar()
            assert bar is not None
            bar.showMessage("Page failed to load", 4000)

    def _sync_url_bar(self, view: QWebEngineView) -> None:
        url_str = view.url().toString()
        if url_str.startswith("parallel://"):
            url_str = ""
        if url_str != self._url_bar.text():
            self._url_bar.setText(url_str)

    def _sync_nav_buttons(self) -> None:
        idx = self._tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            hist = self._tabs[idx].history()
            assert hist is not None
            self._btn_back.setEnabled(hist.canGoBack())
            self._btn_fwd.setEnabled(hist.canGoForward())

    # ── 3×3 Grid ("Parallel View") ────────────────────────────────────

    def _toggle_grid(self) -> None:
        self._is_grid = not self._is_grid
        self._btn_grid.setChecked(self._is_grid)

        if self._is_grid:
            self._rebuild_grid()
            self._content.setCurrentIndex(1)
            self._tab_bar.hide()
            # Staggered fade-in for each card
            self._animate_grid_cards_in()
        else:
            self._fade.flash(ANIM_DURATION_MS)
            self._content.setCurrentIndex(0)
            self._tab_bar.show()
            QTimer.singleShot(100, self._clear_grid)

    def _animate_grid_cards_in(self) -> None:
        """Stagger-fade each grid card into view for a smooth reveal."""
        self._keep_alive.clear()
        n = self._grid_lay.count()
        for i in range(n):
            item = self._grid_lay.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue

            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)

            anim = QPropertyAnimation(eff, b"opacity")
            anim.setDuration(280)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            # Stagger each card
            QTimer.singleShot(i * GRID_STAGGER_MS, anim.start)
            self._keep_alive.append(anim)
            self._keep_alive.append(eff)

    def _rebuild_grid(self) -> None:
        self._clear_grid()
        for i, view in enumerate(self._tabs):
            row, col = divmod(i, 3)
            card = self._make_grid_card(i, view)
            self._grid_lay.addWidget(card, row, col)

        # Fill remaining cells up to at least a full 3×3
        total = len(self._tabs)
        fill_to = max(9, ((total + 2) // 3) * 3)
        for i in range(total, fill_to):
            row, col = divmod(i, 3)
            self._grid_lay.addWidget(self._make_empty_card(), row, col)

    def _make_grid_card(self, index: int, view: QWebEngineView) -> QFrame:
        card = QFrame(objectName="gridCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Header: title + close
        header = QHBoxLayout()
        header.setSpacing(4)

        title_lbl = QLabel(self._titles[index])
        title_lbl.setObjectName("gridTitle")
        title_lbl.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        title_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header.addWidget(title_lbl)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("gridClose")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(
            lambda _checked=False, idx=index: self._grid_close(idx)
        )
        header.addWidget(close_btn)
        lay.addLayout(header)

        # Thumbnail
        pix = view.grab()
        if pix.isNull():
            pix = QPixmap(400, 260)
            pix.fill(Qt.GlobalColor.black)
        else:
            pix = pix.scaled(
                QSize(420, 280),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        thumb = QLabel()
        thumb.setPixmap(pix)
        thumb.setObjectName("gridThumb")
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        lay.addWidget(thumb, 1)

        # URL at bottom
        url_str = view.url().toString()
        if url_str.startswith("parallel://"):
            url_str = "New Tab"
        url_lbl = QLabel(url_str)
        url_lbl.setObjectName("gridUrl")
        url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(url_lbl)

        # Click → select that tab
        card.mousePressEvent = lambda ev, idx=index: self._grid_select(idx)
        return card

    @staticmethod
    def _make_empty_card() -> QFrame:
        f = QFrame(objectName="gridEmpty")
        lay = QVBoxLayout(f)
        lbl = QLabel("Empty")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setObjectName("gridEmptyLabel")
        lay.addWidget(lbl)
        return f

    def _grid_select(self, index: int) -> None:
        self._tab_bar.setCurrentIndex(index)
        self._toggle_grid()

    def _grid_close(self, index: int) -> None:
        self.close_tab(index)

    def _clear_grid(self) -> None:
        while self._grid_lay.count():
            item = self._grid_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # ── Resize handling (keep fade overlay sized correctly) ─────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fade.setGeometry(self._content_wrapper.rect())

    # ── Theme / styling ────────────────────────────────────────────────

    def _apply_theme(self) -> None:  # noqa: C901
        self.setStyleSheet(
            """
            /* ── Window ── */
            QMainWindow { background: #1e1e2e; }

            /* ── Navigation bar ── */
            #nav {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1b1b2b, stop:1 #181825
                );
                border-bottom: 1px solid #313244;
            }

            QPushButton {
                background: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 7px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover {
                background: #45475a; border-color: #6c7086;
            }
            QPushButton:pressed { background: #585b70; }
            QPushButton:checked {
                background: #89b4fa; color: #1e1e2e; border-color: #89b4fa;
            }
            QPushButton:disabled {
                background: #1e1e2e; color: #45475a; border-color: #313244;
            }

            /* ── URL bar ── */
            #urlBar {
                background: #313244; color: #cdd6f4;
                border: 2px solid #45475a; border-radius: 9px;
                padding: 5px 14px; font-size: 13px;
                selection-background-color: #89b4fa;
                selection-color: #1e1e2e;
            }
            #urlBar:focus {
                border-color: #89b4fa;
                background: #292940;
            }

            /* ── Tab bar ── */
            QTabBar {
                background: #181825;
            }
            QTabBar::tab {
                background: #1e1e2e; color: #a6adc8;
                border: 1px solid #313244; border-bottom: none;
                padding: 7px 16px; margin-right: 1px;
                border-top-left-radius: 7px; border-top-right-radius: 7px;
                min-width: 100px; max-width: 220px;
            }
            QTabBar::tab:selected {
                background: #313244; color: #cdd6f4; border-color: #45475a;
            }
            QTabBar::tab:hover:!selected {
                background: #262637; color: #cdd6f4;
            }

            /* ── Status bar ── */
            QStatusBar {
                background: #181825; color: #a6adc8;
                border-top: 1px solid #313244; font-size: 11px;
            }
            QProgressBar {
                background: #313244; border: none; border-radius: 6px;
                text-align: center; color: transparent; font-size: 0;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #74c7ec, stop:1 #89b4fa
                );
                border-radius: 6px;
            }

            /* ── Content area ── */
            QStackedWidget { background: #1e1e2e; }
            #gridScroll { background: #1e1e2e; border: none; }

            /* ── Grid cards ── */
            #gridCard {
                background: #313244;
                border: 2px solid #45475a;
                border-radius: 12px;
            }
            #gridCard:hover {
                border-color: #89b4fa;
                background: #383850;
            }

            #gridTitle {
                color: #cdd6f4; font-size: 11px; font-weight: 600;
                background: transparent; border: none; padding-left: 4px;
            }
            #gridClose {
                background: #45475a; color: #cdd6f4; border: none;
                border-radius: 11px; font-size: 11px;
            }
            #gridClose:hover { background: #f38ba8; color: #1e1e2e; }

            #gridThumb {
                border: none; background: #11111b; border-radius: 8px;
            }
            #gridUrl {
                color: #585b70; font-size: 10px; border: none;
                background: transparent; padding: 2px;
            }

            /* ── Empty grid slots ── */
            #gridEmpty {
                background: #181825;
                border: 2px dashed #313244;
                border-radius: 12px;
                min-height: 200px;
            }
            #gridEmptyLabel {
                color: #45475a; font-size: 13px; border: none;
            }
            """
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Parallel")
    window = BrowserWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
