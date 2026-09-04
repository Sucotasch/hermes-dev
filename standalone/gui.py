# -*- coding: utf-8 -*-
"""Hermes Deep Research — unified GUI (PyQt5).

Launch: python standalone/gui.py
Requires: pip install PyQt5
"""
import sys
import os
import time
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

# Ensure standalone/ is importable
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox,
        QProgressBar, QTextEdit, QCheckBox, QFileDialog, QFrame,
        QMessageBox, QSizePolicy,
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt5.QtGui import QFont, QTextCursor
except ImportError:
    print("PyQt5 not installed. Run: pip install PyQt5")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────
_REPO_ROOT = _HERE.parent
_HERMES_HOME = Path.home() / ".hermes"
_LOG_DIR = _HERE / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# ── Provider presets ─────────────────────────────────────────────────────────
PROVIDERS = {
    "Ollama": "http://localhost:11434",
    "LMStudio": "http://localhost:1234",
    "Custom": "http://127.0.0.1:8888",
}

# ── Progress stage mapping ───────────────────────────────────────────────────
_STAGE_MAP = {
    "classif": (0, 5),
    "enrich": (5, 8),
    "search": (8, 35),
    "blocklist": (35, 37),
    "validat": (37, 55),
    "level 2": (55, 65),
    "deep-read": (65, 80),
    "reading": (65, 80),
    "skip": (65, 80),
    "image": (80, 85),
    "evidence": (85, 90),
    "synth": (90, 97),
    "building report": (97, 100),
    "total": (100, 100),
}


def _estimate_progress(msg):
    """Map a log message to (percent, stage_label)."""
    low = msg.lower()
    for key, (lo, hi) in _STAGE_MAP.items():
        if key in low:
            return (lo + hi) // 2, key
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# Connection Check Thread
# ══════════════════════════════════════════════════════════════════════════════
class ConnectionCheckThread(QThread):
    """Test LLM server connectivity and fetch available models."""

    result = pyqtSignal(bool, str, list)  # (ok, message, model_names)

    def __init__(self, server_url):
        super().__init__()
        self.server_url = server_url.rstrip("/")

    def run(self):
        import urllib.request
        import urllib.error
        import json

        models = []

        # Try /v1/models first (universal OpenAI-compatible)
        for endpoint in ["/v1/models", "/api/tags", "/health"]:
            url = f"{self.server_url}{endpoint}"
            try:
                req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read().decode("utf-8")
                    if endpoint == "/v1/models":
                        # OpenAI format: {"data": [{"id": "model-name", ...}]}
                        # llama.cpp format: {"models": [{"name": "Local Model", ...}]}
                        try:
                            parsed = json.loads(data)
                            if "data" in parsed:
                                models = [m.get("id", "?") for m in parsed["data"] if m.get("id")]
                            elif "models" in parsed:
                                models = [m.get("name", m.get("model", "?")) for m in parsed["models"]]
                        except (json.JSONDecodeError, KeyError):
                            pass
                    elif endpoint == "/api/tags":
                        # Ollama format: {"models": [{"name": "llama3:latest", ...}]}
                        try:
                            parsed = json.loads(data)
                            models = [m.get("name", "?") for m in parsed.get("models", [])]
                        except (json.JSONDecodeError, KeyError):
                            pass

                    if models:
                        self.result.emit(True, f"Connected — {len(models)} model(s)", models)
                    else:
                        self.result.emit(True, "Connected — no models listed", [])
                    return
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    self.result.emit(True, f"Server responding (HTTP {e.code})", [])
                    return
            except (urllib.error.URLError, OSError, TimeoutError):
                continue

        self.result.emit(False, f"Cannot reach {self.server_url}", [])


# ══════════════════════════════════════════════════════════════════════════════
# Hermes Check Thread
# ══════════════════════════════════════════════════════════════════════════════
class HermesCheckThread(QThread):
    """Check tool availability and optionally run restore."""

    status = pyqtSignal(str)     # status text
    log = pyqtSignal(str)        # detail log lines
    finished_ok = pyqtSignal(bool)  # True if all tools loaded

    TOOL_NAMES = [
        "web_search_deep",
        "web_expand_and_fetch",
        "visit_website_tool",
        "image_search",
        "web_deep_research",
    ]

    def __init__(self, run_restore=False):
        super().__init__()
        self.run_restore = run_restore

    def run(self):
        self.status.emit("Checking tools...")
        self.log.emit("Importing hermes-agent tools registry...")

        # Add hermes-agent to sys.path so we can import tools.registry
        hermes_agent_dir = str(_HERMES_HOME / "hermes-agent")
        tools_dir = str(_HERMES_HOME / "hermes-agent" / "tools")
        for d in [hermes_agent_dir, tools_dir]:
            if d not in sys.path:
                sys.path.insert(0, d)

        try:
            from tools.registry import discover_builtin_tools, registry
            discover_builtin_tools(tools_dir)
            self.log.emit("Registry discovered. Checking web tools...")
        except ImportError as e:
            self.status.emit("Cannot import hermes registry")
            self.log.emit(f"Import error: {e}")
            self.log.emit("Hermes may not be installed at ~/.hermes/")
            self.finished_ok.emit(False)
            return

        # Check each tool
        results = {}
        for name in self.TOOL_NAMES:
            try:
                entry = registry.get_entry(name)
                if entry is None:
                    results[name] = False
                    self.log.emit(f"  {name}: NOT REGISTERED")
                elif entry.check_fn and not entry.check_fn():
                    results[name] = False
                    self.log.emit(f"  {name}: check_fn FAILED")
                else:
                    results[name] = True
                    self.log.emit(f"  {name}: OK")
            except Exception as e:
                results[name] = False
                self.log.emit(f"  {name}: ERROR - {e}")

        ok_count = sum(1 for v in results.values() if v)
        total = len(results)

        if ok_count == total:
            self.status.emit(f"All {total} tools loaded")
            self.finished_ok.emit(True)
            return

        missing = [n for n, v in results.items() if not v]
        self.status.emit(f"{ok_count}/{total} tools loaded — missing: {', '.join(missing)}")

        if not self.run_restore:
            self.finished_ok.emit(False)
            return

        # Run restore
        self.log.emit("Running restore.ps1...")
        restore_script = str(_REPO_ROOT / "restore.ps1")
        if not Path(restore_script).exists():
            self.log.emit(f"restore.ps1 not found at {restore_script}")
            self.finished_ok.emit(False)
            return

        try:
            # 300s: restore also installs missing venv deps on first run.
            result = subprocess.run(
                ["powershell.exe", "-ExecutionPolicy", "Bypass",
                 "-File", restore_script, "-SkipBackup", "-NoStopHermes"],
                capture_output=True, text=True, timeout=300,
            )
            for line in result.stdout.splitlines():
                self.log.emit(f"  {line}")
            if result.returncode != 0:
                self.log.emit(f"Restore exited with code {result.returncode}")
                if result.stderr:
                    self.log.emit(f"  stderr: {result.stderr[:500]}")
            self.status.emit("Restore complete — re-checking...")
        except subprocess.TimeoutExpired:
            self.log.emit("Restore timed out (300s)")
        except Exception as e:
            self.log.emit(f"Restore error: {e}")

        # Re-check after restore
        self.log.emit("Re-checking tools after restore...")
        try:
            discover_builtin_tools(tools_dir)
        except Exception:
            pass

        results2 = {}
        for name in self.TOOL_NAMES:
            try:
                entry = registry.get_entry(name)
                results2[name] = entry is not None
            except Exception:
                results2[name] = False

        ok2 = sum(1 for v in results2.values() if v)
        self.status.emit(f"After restore: {ok2}/{total} tools loaded")
        self.finished_ok.emit(ok2 == total)


# ══════════════════════════════════════════════════════════════════════════════
# Research Thread
# ══════════════════════════════════════════════════════════════════════════════
class ResearchThread(QThread):
    """Run deep research pipeline in background."""

    progress = pyqtSignal(int, str)   # (percent, stage label)
    log_line = pyqtSignal(str)        # raw log line
    finished_ok = pyqtSignal(dict)    # result dict
    error = pyqtSignal(str)           # error message

    def __init__(self, query, server_url, max_validate, output_dir, model="local",
                 proxy_enabled=False, proxy_url="http://127.0.0.1:2080",
                 top_n=30, images_count=30, llm_sources=20, max_variants=6, max_imgs_per_page=5,
                 search_count=100, query_type=None):
        super().__init__()
        self.query = query
        self.server_url = server_url
        self.max_validate = max_validate
        self.output_dir = output_dir
        self.model = model
        self.proxy_enabled = proxy_enabled
        self.proxy_url = proxy_url
        self.top_n = top_n
        self.images_count = images_count
        self.llm_sources = llm_sources
        self.max_variants = max_variants
        self.max_imgs_per_page = max_imgs_per_page
        self.search_count = search_count
        self.query_type = query_type
        self._cancelled = False

    def run(self):
        try:
            from orchestrator import run_deep_research

            def log_callback(msg):
                if self._cancelled:
                    return
                self.log_line.emit(msg)
                pct, label = _estimate_progress(msg)
                if pct is not None:
                    self.progress.emit(pct, label)

            self.progress.emit(0, "Starting...")
            result = run_deep_research(
                query=self.query,
                server_url=self.server_url,
                max_validate=self.max_validate,
                log=log_callback,
                model=self.model,
                proxy_enabled=self.proxy_enabled,
                proxy_url=self.proxy_url,
                top_n=self.top_n,
                images_count=self.images_count,
                llm_sources=self.llm_sources,
                max_variants=self.max_variants,
                max_imgs_per_page=self.max_imgs_per_page,
                search_count=self.search_count,
                query_type=self.query_type,
            )

            if self._cancelled:
                return

            # Save report
            out_dir = Path(self.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = self.query.lower().strip()
            slug = __import__("re").sub(r"[^\w\s-]", "", slug)
            slug = __import__("re").sub(r"[\s_]+", "_", slug)[:60]
            date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            out_path = out_dir / f"{slug}_{date_str}.md"
            out_path.write_text(result["report"], encoding="utf-8")

            result["saved_path"] = str(out_path)
            self.progress.emit(100, "Done")
            self.finished_ok.emit(result)

        except Exception as e:
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    def cancel(self):
        self._cancelled = True


# ══════════════════════════════════════════════════════════════════════════════
# Main Window
# ══════════════════════════════════════════════════════════════════════════════
class HermesGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hermes Deep Research")
        self.setMinimumSize(680, 620)
        self._research_thread = None
        self._log_file = None
        self._log_enabled = False
        self._init_ui()

    # ── UI Setup ─────────────────────────────────────────────────────────────
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Hermes Mode ──────────────────────────────────────────────────────
        grp_hermes = QGroupBox("Hermes Mode")
        h_lay = QHBoxLayout(grp_hermes)

        self.btn_check = QPushButton("Check & Restore")
        self.btn_check.setMinimumWidth(140)
        self.btn_check.clicked.connect(self._on_check_restore)
        h_lay.addWidget(self.btn_check)

        self.lbl_hermes_status = QLabel("Not checked")
        self.lbl_hermes_status.setStyleSheet("color: gray;")
        h_lay.addWidget(self.lbl_hermes_status, 1)

        root.addWidget(grp_hermes)

        # ── Standalone Mode ──────────────────────────────────────────────────
        grp_sa = QGroupBox("Standalone Mode — LLM Provider")
        sa_lay = QVBoxLayout(grp_sa)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Provider:"))
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(PROVIDERS.keys())
        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)
        self.cmb_provider.setMinimumWidth(120)
        row1.addWidget(self.cmb_provider)

        row1.addWidget(QLabel("URL:"))
        self.txt_server = QLineEdit(PROVIDERS["Ollama"])
        self.txt_server.setPlaceholderText("http://host:port")
        row1.addWidget(self.txt_server, 1)

        self.btn_test_conn = QPushButton("Test")
        self.btn_test_conn.setMaximumWidth(50)
        self.btn_test_conn.setToolTip("Test connection to LLM server")
        self.btn_test_conn.clicked.connect(self._on_test_connection)
        row1.addWidget(self.btn_test_conn)

        self.lbl_conn_status = QLabel("")
        self.lbl_conn_status.setMinimumWidth(160)
        row1.addWidget(self.lbl_conn_status)

        sa_lay.addLayout(row1)

        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("Model:"))
        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.setMinimumWidth(250)
        self.cmb_model.addItem("local")
        self.cmb_model.setToolTip("Select model or type custom name")
        row_model.addWidget(self.cmb_model, 1)
        sa_lay.addLayout(row_model)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Output dir:"))
        self.txt_output = QLineEdit(str(Path("reports").resolve()))
        row2.addWidget(self.txt_output, 1)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._on_browse_output)
        row2.addWidget(btn_browse)

        row2.addWidget(QLabel("Validate:"))
        self.spin_validate = QSpinBox()
        self.spin_validate.setRange(10, 500)
        self.spin_validate.setValue(100)
        self.spin_validate.setMinimumWidth(70)
        row2.addWidget(self.spin_validate)
        sa_lay.addLayout(row2)

        self._updating_validate = False

        row_depth = QHBoxLayout()
        row_depth.addWidget(QLabel("Deep-read:"))
        self.spin_top_n = QSpinBox()
        self.spin_top_n.setRange(5, 50)
        self.spin_top_n.setValue(30)
        self.spin_top_n.setMinimumWidth(60)
        self.spin_top_n.valueChanged.connect(self._on_depth_changed)
        row_depth.addWidget(self.spin_top_n)

        row_depth.addWidget(QLabel("Report src:"))
        self.spin_images = QSpinBox()
        self.spin_images.setRange(0, 200)
        self.spin_images.setValue(30)
        self.spin_images.setSpecialValueText("0=all")
        self.spin_images.setToolTip("Max sources/images in report (0 = all collected)")
        self.spin_images.setMinimumWidth(60)
        row_depth.addWidget(self.spin_images)

        row_depth.addWidget(QLabel("LLM src:"))
        self.spin_llm_src = QSpinBox()
        self.spin_llm_src.setRange(3, 30)
        self.spin_llm_src.setValue(20)
        self.spin_llm_src.setToolTip("Sources for LLM synthesis (separate from report)")
        self.spin_llm_src.setMinimumWidth(60)
        self.spin_llm_src.valueChanged.connect(self._on_depth_changed)
        row_depth.addWidget(self.spin_llm_src)

        row_depth.addWidget(QLabel("Variants:"))
        self.spin_variants = QSpinBox()
        self.spin_variants.setRange(1, 10)
        self.spin_variants.setValue(6)
        self.spin_variants.setMinimumWidth(60)
        row_depth.addWidget(self.spin_variants)

        row_depth.addWidget(QLabel("Search:"))
        self.spin_search_count = QSpinBox()
        self.spin_search_count.setRange(10, 200)
        self.spin_search_count.setValue(100)
        self.spin_search_count.setToolTip("Max URLs per search query variant")
        self.spin_search_count.setMinimumWidth(60)
        row_depth.addWidget(self.spin_search_count)

        row_depth.addWidget(QLabel("Page imgs:"))
        self.spin_max_imgs = QSpinBox()
        self.spin_max_imgs.setRange(0, 200)
        self.spin_max_imgs.setValue(5)
        self.spin_max_imgs.setSpecialValueText("0=all")
        self.spin_max_imgs.setToolTip("Max images extracted per page (0 = all)")
        self.spin_max_imgs.setMinimumWidth(60)
        row_depth.addWidget(self.spin_max_imgs)

        row_depth.addWidget(QLabel("Preset:"))
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(["Balanced", "Minimal", "Visual", "Maximum"])
        self.cmb_preset.setToolTip("Quick parameter presets")
        self.cmb_preset.setMinimumWidth(90)
        self.cmb_preset.currentTextChanged.connect(self._on_preset_changed)
        row_depth.addWidget(self.cmb_preset)

        btn_reset = QPushButton("Reset")
        btn_reset.setToolTip("Reset pipeline parameters to defaults")
        btn_reset.setMaximumWidth(60)
        btn_reset.clicked.connect(self._on_reset_params)
        row_depth.addWidget(btn_reset)

        sa_lay.addLayout(row_depth)

        row_proxy = QHBoxLayout()
        self.chk_proxy = QCheckBox("Proxy")
        self.chk_proxy.setChecked(True)
        self.chk_proxy.toggled.connect(self._on_proxy_changed)
        row_proxy.addWidget(self.chk_proxy)

        row_proxy.addWidget(QLabel("URL:"))
        self.txt_proxy = QLineEdit("http://127.0.0.1:2080")
        self.txt_proxy.setPlaceholderText("http://host:port")
        self.txt_proxy.setMaximumWidth(220)
        self.txt_proxy.editingFinished.connect(self._on_proxy_changed)
        row_proxy.addWidget(self.txt_proxy)

        row_proxy.addStretch()
        sa_lay.addLayout(row_proxy)

        root.addWidget(grp_sa)

        # ── Search ───────────────────────────────────────────────────────────
        grp_search = QGroupBox("Search")
        s_lay = QHBoxLayout(grp_search)

        self.cmb_query_type = QComboBox()
        self.cmb_query_type.addItems(
            ["Auto", "general", "person", "visual", "technical", "news",
             "historical", "comparison", "fact", "art", "education",
             "science", "video"])
        self.cmb_query_type.setToolTip(
            "Query intent. Auto = LLM classifies (needs an LLM server).\n"
            "A fixed type skips classification and works with no LLM:\n"
            "the report is then built without the synthesis section.")
        self.cmb_query_type.setMaximumWidth(110)
        s_lay.addWidget(self.cmb_query_type)

        self.txt_query = QLineEdit()
        self.txt_query.setPlaceholderText("Enter research query...")
        self.txt_query.returnPressed.connect(self._on_research)
        s_lay.addWidget(self.txt_query, 1)

        self.btn_research = QPushButton("Research")
        self.btn_research.setMinimumWidth(100)
        self.btn_research.clicked.connect(self._on_research)
        s_lay.addWidget(self.btn_research)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setMinimumWidth(80)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        s_lay.addWidget(self.btn_cancel)

        root.addWidget(grp_search)

        # ── Progress ─────────────────────────────────────────────────────────
        grp_prog = QGroupBox("Progress")
        p_lay = QVBoxLayout(grp_prog)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        p_lay.addWidget(self.progress_bar)

        self.lbl_stage = QLabel("Idle")
        self.lbl_stage.setStyleSheet("color: gray;")
        p_lay.addWidget(self.lbl_stage)

        root.addWidget(grp_prog)

        # ── Log ──────────────────────────────────────────────────────────────
        grp_log = QGroupBox("Log")
        l_lay = QVBoxLayout(grp_log)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 8))
        self.txt_log.setMaximumHeight(180)
        l_lay.addWidget(self.txt_log)

        log_row = QHBoxLayout()
        self.chk_log = QCheckBox("File logging")
        self.chk_log.toggled.connect(self._on_log_toggled)
        log_row.addWidget(self.chk_log)

        self.lbl_log_path = QLabel("")
        self.lbl_log_path.setStyleSheet("color: gray; font-size: 10px;")
        log_row.addWidget(self.lbl_log_path, 1)

        btn_log_dir = QPushButton("Open log folder")
        btn_log_dir.setMaximumWidth(120)
        btn_log_dir.clicked.connect(self._on_open_log_dir)
        log_row.addWidget(btn_log_dir)

        l_lay.addLayout(log_row)
        root.addWidget(grp_log)

        # ── Status bar ───────────────────────────────────────────────────────
        grp_status = QGroupBox("Result")
        st_lay = QHBoxLayout(grp_status)

        self.lbl_result = QLabel("No results yet")
        self.lbl_result.setWordWrap(True)
        st_lay.addWidget(self.lbl_result, 1)

        self.btn_open_report = QPushButton("Open Report")
        self.btn_open_report.setEnabled(False)
        self.btn_open_report.clicked.connect(self._on_open_report)
        st_lay.addWidget(self.btn_open_report)

        root.addWidget(grp_status)

        self._last_report_path = None

    # ── Handlers ─────────────────────────────────────────────────────────────
    def _on_provider_changed(self, name):
        url = PROVIDERS.get(name, "")
        if url:
            self.txt_server.setText(url)
        self.lbl_conn_status.setText("")
        self.lbl_conn_status.setStyleSheet("")
        self.cmb_model.clear()
        self.cmb_model.addItem("local")

    def _on_depth_changed(self):
        """Auto-raise Validate when Deep-read or LLM sources exceeds it."""
        if self._updating_validate:
            return
        needed = max(self.spin_top_n.value(), self.spin_llm_src.value())
        if needed > self.spin_validate.value():
            self._updating_validate = True
            self.spin_validate.setValue(needed)
            self._updating_validate = False

    def _on_reset_params(self):
        """Reset pipeline parameters to defaults."""
        self.spin_validate.setValue(100)
        self.spin_top_n.setValue(30)
        self.spin_images.setValue(30)
        self.spin_llm_src.setValue(20)
        self.spin_variants.setValue(6)
        self.spin_search_count.setValue(100)
        self.spin_max_imgs.setValue(5)

    def _on_preset_changed(self, preset):
        """Apply parameter preset."""
        presets = {
            "Minimal": {"top_n": 10, "images": 10, "llm_src": 5, "variants": 3, "search": 50, "max_imgs": 3},
            "Balanced": {"top_n": 30, "images": 30, "llm_src": 20, "variants": 6, "search": 100, "max_imgs": 5},
            "Visual": {"top_n": 30, "images": 0, "llm_src": 20, "variants": 6, "search": 100, "max_imgs": 10},
            "Maximum": {"top_n": 50, "images": 0, "llm_src": 30, "variants": 10, "search": 200, "max_imgs": 0},
        }
        p = presets.get(preset, presets["Balanced"])
        self.spin_top_n.setValue(p["top_n"])
        self.spin_images.setValue(p["images"])
        self.spin_llm_src.setValue(p["llm_src"])
        self.spin_variants.setValue(p["variants"])
        self.spin_search_count.setValue(p["search"])
        self.spin_max_imgs.setValue(p["max_imgs"])

    def _on_proxy_changed(self):
        """Save proxy settings to ~/.hermes/proxy.env for Hermes mode."""
        from pathlib import Path
        proxy_dir = Path.home() / ".hermes"
        proxy_file = proxy_dir / "proxy.env"
        try:
            if self.chk_proxy.isChecked():
                url = self.txt_proxy.text().strip()
                if url:
                    proxy_dir.mkdir(parents=True, exist_ok=True)
                    proxy_file.write_text(url)
                else:
                    proxy_file.unlink(missing_ok=True)
            else:
                proxy_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _on_test_connection(self):
        url = self.txt_server.text().strip()
        if not url:
            self.lbl_conn_status.setText("No URL")
            self.lbl_conn_status.setStyleSheet("color: red;")
            return
        self.btn_test_conn.setEnabled(False)
        self.lbl_conn_status.setText("Testing...")
        self.lbl_conn_status.setStyleSheet("color: orange;")
        self._conn_thread = ConnectionCheckThread(url)
        self._conn_thread.result.connect(self._on_conn_result)
        self._conn_thread.start()

    def _on_conn_result(self, ok, msg, models):
        self.btn_test_conn.setEnabled(True)
        self.lbl_conn_status.setText(msg)
        self.lbl_conn_status.setStyleSheet("color: green;" if ok else "color: red;")
        # Populate model dropdown
        self.cmb_model.clear()
        if models:
            self.cmb_model.addItems(models)
        else:
            self.cmb_model.addItem("local")

    def _on_browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory",
                                              self.txt_output.text())
        if d:
            self.txt_output.setText(d)

    def _on_check_restore(self):
        self.btn_check.setEnabled(False)
        self.lbl_hermes_status.setText("Checking...")
        self.lbl_hermes_status.setStyleSheet("color: orange;")
        self.txt_log.clear()
        self._log_to_gui("[hermes] Starting health check...")

        self._check_thread = HermesCheckThread(run_restore=True)
        self._check_thread.status.connect(self._on_check_status)
        self._check_thread.log.connect(self._on_check_log)
        self._check_thread.finished_ok.connect(self._on_check_done)
        self._check_thread.start()

    def _on_check_status(self, text):
        self.lbl_hermes_status.setText(text)

    def _on_check_log(self, text):
        self._log_to_gui(text)

    def _on_check_done(self, ok):
        self.btn_check.setEnabled(True)
        if ok:
            self.lbl_hermes_status.setStyleSheet("color: green;")
        else:
            self.lbl_hermes_status.setStyleSheet("color: red;")

    def _on_research(self):
        query = self.txt_query.text().strip()
        if not query:
            return
        server = self.txt_server.text().strip()
        if not server:
            QMessageBox.warning(self, "Missing URL", "Enter LLM server URL.")
            return

        self.btn_research.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.txt_query.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_stage.setText("Starting...")
        self.txt_log.clear()

        model = self.cmb_model.currentText().strip() or "local"
        proxy_enabled = self.chk_proxy.isChecked()
        proxy_url = self.txt_proxy.text().strip() or "http://127.0.0.1:2080"
        # "Auto" → None → LLM classifies (or "general" fallback when the
        # LLM server is absent — the pipeline runs either way).
        qtype_choice = self.cmb_query_type.currentText()
        query_type = None if qtype_choice == "Auto" else qtype_choice
        self._research_thread = ResearchThread(
            query=query,
            server_url=server,
            max_validate=self.spin_validate.value(),
            output_dir=self.txt_output.text(),
            model=model,
            proxy_enabled=proxy_enabled,
            proxy_url=proxy_url,
            top_n=self.spin_top_n.value(),
            images_count=self.spin_images.value(),
            llm_sources=self.spin_llm_src.value(),
            max_variants=self.spin_variants.value(),
            max_imgs_per_page=self.spin_max_imgs.value(),
            search_count=self.spin_search_count.value(),
            query_type=query_type,
        )
        self._research_thread.progress.connect(self._on_progress)
        self._research_thread.log_line.connect(self._on_log_line)
        self._research_thread.finished_ok.connect(self._on_research_done)
        self._research_thread.error.connect(self._on_research_error)
        self._research_thread.start()

    def _on_cancel(self):
        if self._research_thread and self._research_thread.isRunning():
            self._research_thread.cancel()
            self.lbl_stage.setText("Cancelling...")
            self.btn_cancel.setEnabled(False)

    def _on_progress(self, pct, label):
        self.progress_bar.setValue(pct)
        if label:
            self.lbl_stage.setText(label.title())

    def _on_log_line(self, text):
        self._log_to_gui(text)

    def _on_research_done(self, result):
        self.btn_research.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.txt_query.setEnabled(True)

        stats = result.get("stats", {})
        path = result.get("saved_path", "")
        total = stats.get("total_time", 0)
        sources = stats.get("evidence_pages", 0)
        images = stats.get("images", 0)
        llm_note = "" if stats.get("llm", True) else " | No LLM (report without synthesis)"

        self.lbl_result.setText(
            f"Saved: {path}\n"
            f"Sources: {sources} | Images: {images} | Time: {total}s{llm_note}"
        )
        self.lbl_result.setStyleSheet("color: green;")
        self._last_report_path = path
        self.btn_open_report.setEnabled(bool(path))

    def _on_research_error(self, msg):
        self.btn_research.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.txt_query.setEnabled(True)
        self.lbl_result.setText(f"Error: {msg[:300]}")
        self.lbl_result.setStyleSheet("color: red;")
        self.lbl_stage.setText("Failed")

    def _on_log_toggled(self, checked):
        self._log_enabled = checked
        if checked:
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            log_path = _LOG_DIR / f"research_{ts}.log"
            self._log_file = open(log_path, "w", encoding="utf-8")
            self.lbl_log_path.setText(str(log_path))
        else:
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self.lbl_log_path.setText("")

    def _on_open_log_dir(self):
        os.startfile(str(_LOG_DIR))

    def _on_open_report(self):
        if self._last_report_path and Path(self._last_report_path).exists():
            os.startfile(self._last_report_path)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _log_to_gui(self, text):
        self.txt_log.append(text)
        cursor = self.txt_log.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.txt_log.setTextCursor(cursor)
        if self._log_enabled and self._log_file:
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_file.write(f"[{ts}] {text}\n")
            self._log_file.flush()

    def closeEvent(self, event):
        if self._research_thread and self._research_thread.isRunning():
            self._research_thread.cancel()
            self._research_thread.wait(3000)
        if self._log_file:
            self._log_file.close()
        event.accept()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = HermesGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
