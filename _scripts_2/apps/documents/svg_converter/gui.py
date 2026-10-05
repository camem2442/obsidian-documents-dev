"""Modern PyQt6 Desktop GUI for SVG Converter."""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QByteArray, QEvent, QObject, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from .engine import SvgConverter, SvgOptions, SvgResult
except (ImportError, ValueError):
    from engine import SvgConverter, SvgOptions, SvgResult


class ConversionWorker(QThread):
    finished_signal = pyqtSignal(object)  # SvgResult
    error_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)

    def __init__(self, image_path: Path, context: str, hint: str, model: str):
        super().__init__()
        self.image_path = image_path
        self.context = context
        self.hint = hint
        self.model = model

    def run(self):
        try:
            opts = SvgOptions(model=self.model, prompt_hint=self.hint)
            converter = SvgConverter(default_options=opts)
            res = converter.convert_image(
                self.image_path,
                context_text=self.context,
                options=opts,
                log_callback=lambda msg: self.log_signal.emit(msg),
            )
            self.finished_signal.emit(res)
        except Exception as e:
            self.error_signal.emit(str(e))


class TextEditPasteFilter(QObject):
    """Event filter for text edits to redirect image paste (Cmd+V) to the image converter."""

    def __init__(self, on_paste_image, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._on_paste_image = on_paste_image

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            is_paste = event.matches(QKeySequence.StandardKey.Paste) or (
                event.key() == Qt.Key.Key_V
                and bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
            )
            if is_paste:
                cb = QApplication.clipboard()
                mime = cb.mimeData()
                if mime:
                    # If clipboard has an image (or file URLs to images) and NO plain text, route to image paste
                    if (mime.hasImage() and not mime.hasText()) or (mime.hasUrls() and not mime.hasText()):
                        if self._on_paste_image():
                            return True
        return super().eventFilter(watched, event)


class DropArea(QLabel):
    file_dropped = pyqtSignal(Path)
    paste_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("🖼️ 이미지 파일을 여기에 드래그하거나 붙여넣으세요 (⌘V)\n또는 아래 버튼을 눌러 선택하세요\n(PNG, JPG, WebP)")
        self.setStyleSheet("""
            DropArea {
                border: 2px dashed #4b5563;
                border-radius: 12px;
                background-color: #1e1e2e;
                color: #9ca3af;
                font-size: 14px;
                padding: 24px;
            }
            DropArea:hover {
                border-color: #3b82f6;
                color: #e5e7eb;
            }
        """)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                self.file_dropped.emit(p)
                event.acceptProposedAction()
                return

    def keyPressEvent(self, event: QKeyEvent):
        is_paste = event.matches(QKeySequence.StandardKey.Paste) or (
            event.key() == Qt.Key.Key_V
            and bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        )
        if is_paste:
            self.paste_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SvgConverterWindow(QMainWindow):
    def __init__(self, initial_image: Optional[Path] = None, *, log_dir: Optional[Path] = None):
        super().__init__()
        self.setWindowTitle("SVG Converter — Academic Graph & Diagram Vectorizer")
        self.resize(1180, 780)
        self.current_image_path: Optional[Path] = None
        self.current_svg: Optional[str] = None
        self.worker: Optional[ConversionWorker] = None

        self.start_timestamp: float = 0.0
        self.latest_log_msg: str = ""
        self._log_dir = Path(log_dir) if log_dir is not None else None
        self.timer_stopwatch = QTimer(self)
        self.timer_stopwatch.setInterval(100)
        self.timer_stopwatch.timeout.connect(self._update_stopwatch)

        self._setup_ui()
        self._setup_menu_bar()
        self._setup_shortcuts()
        self._apply_dark_theme()

        if initial_image and Path(initial_image).is_file():
            self._load_image(Path(initial_image))

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # 1. Top Header Bar
        top_bar = QHBoxLayout()
        title_lbl = QLabel("📊 SVG Converter")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #f3f4f6;")
        sub_lbl = QLabel("학술 그래프 및 다이어그램을 고품질 Obsidian 벡터 SVG로 변환")
        sub_lbl.setStyleSheet("font-size: 12px; color: #9ca3af; margin-left: 8px;")
        top_bar.addWidget(title_lbl)
        top_bar.addWidget(sub_lbl)
        top_bar.addStretch()

        model_lbl = QLabel("모델:")
        model_lbl.setStyleSheet("color: #d1d5db; font-weight: 500;")
        self.model_combo = QComboBox()
        self.model_combo.addItems(["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"])
        self.model_combo.setCurrentText("gemini-3.8-flash")
        top_bar.addWidget(model_lbl)
        top_bar.addWidget(self.model_combo)
        root_layout.addLayout(top_bar)

        # 2. Main Horizontal Splitter (Left: Source / Right: Output)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Left Panel: Input & Settings ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self._load_image)
        self.drop_area.paste_requested.connect(self._paste_from_clipboard)
        left_layout.addWidget(self.drop_area, stretch=3)

        btn_row = QHBoxLayout()
        self.btn_select_file = QPushButton("📂 이미지 열기...")
        self.btn_select_file.clicked.connect(self._browse_image)
        self.btn_paste = QPushButton("📋 붙여넣기 (⌘V)")
        self.btn_paste.setToolTip("클립보드에 복사된 이미지(스크린샷 등)를 붙여넣습니다 (단축키: ⌘V / Ctrl+V)")
        self.btn_paste.clicked.connect(self._paste_from_clipboard)
        self.lbl_file_info = QLabel("선택된 파일 없음")
        self.lbl_file_info.setStyleSheet("color: #9ca3af; font-size: 12px;")
        btn_row.addWidget(self.btn_select_file)
        btn_row.addWidget(self.btn_paste)
        btn_row.addWidget(self.lbl_file_info, stretch=1)
        left_layout.addLayout(btn_row)

        ctx_lbl = QLabel("맥락 텍스트 / 교재 설명 (선택 사항):")
        ctx_lbl.setStyleSheet("color: #d1d5db; font-size: 12px; font-weight: 500;")
        left_layout.addWidget(ctx_lbl)

        self.edit_context = QPlainTextEdit()
        self.edit_context.setPlaceholderText("그래프의 배경 맥락, 변수 기호 설명(예: x는 실질GDP, y는 소비) 등을 적으면 변환 정확도가 극대화됩니다.")
        self.edit_context.setMaximumHeight(90)
        left_layout.addWidget(self.edit_context)

        hint_lbl = QLabel("추가 지침 (선택 사항):")
        hint_lbl.setStyleSheet("color: #d1d5db; font-size: 12px; font-weight: 500;")
        left_layout.addWidget(hint_lbl)

        self.edit_hint = QPlainTextEdit()
        self.edit_hint.setPlaceholderText("예: '두 곡선의 색상을 파란색과 빨간색으로 대비되게 표현해줘'")
        self.edit_hint.setMaximumHeight(45)
        left_layout.addWidget(self.edit_hint)

        # Event filter so Cmd+V with image inside text edits pastes into converter
        self._paste_filter = TextEditPasteFilter(self._paste_from_clipboard, self)
        self.edit_context.installEventFilter(self._paste_filter)
        self.edit_hint.installEventFilter(self._paste_filter)

        # Quick Preset Chips
        preset_lbl = QLabel("빠른 프롬프트 프리셋 (클릭하여 추가):")
        preset_lbl.setStyleSheet("color: #9ca3af; font-size: 11px; margin-top: 4px;")
        left_layout.addWidget(preset_lbl)

        preset_grid = QVBoxLayout()
        preset_row1 = QHBoxLayout()
        preset_row2 = QHBoxLayout()

        btn_p_econ = QPushButton("📈 경제학 곡선 (균형/이동)")
        btn_p_math = QPushButton("📐 수학 직교좌표계 (f(x))")
        btn_p_stat = QPushButton("📊 통계 산점도 (추세선)")
        btn_p_diag = QPushButton("🔄 개념/구조 다이어그램")

        for pb in (btn_p_econ, btn_p_math, btn_p_stat, btn_p_diag):
            pb.setStyleSheet("""
                QPushButton {
                    font-size: 11px;
                    padding: 4px 8px;
                    background-color: #1f2937;
                    border: 1px solid #374151;
                    border-radius: 4px;
                    color: #d1d5db;
                }
                QPushButton:hover { background-color: #374151; color: #60a5fa; }
            """)

        def _append_hint(text: str):
            cur = self.edit_hint.toPlainText().strip()
            if cur:
                self.edit_hint.setPlainText(f"{cur}, {text}")
            else:
                self.edit_hint.setPlainText(text)

        btn_p_econ.clicked.connect(lambda: _append_hint("경제학 2D 그래프: 원점(0,0), 절편 및 축 라벨, x₁, x₂, p₁, p₂, y*, Q* 등 변수와 수식은 KaTeX 세리프 이탤릭 스타일로 정밀 표기, 균형점 E, 곡선 이동(우측 초록, 좌측 빨강) 명확히 표현"))
        btn_p_math.clicked.connect(lambda: _append_hint("수학 직교좌표계: x/y축 화살표, 원점 O, 함수 곡선 f(x) 및 점선 보조선, 수식과 변수(x, y)는 KaTeX 세리프 이탤릭 스타일로 정확한 라벨링"))
        btn_p_stat.clicked.connect(lambda: _append_hint("통계 도표: 분산된 데이터 점들과 회귀 추세선(우상향/우하향) 및 상관관계 강조, 통계 기호(μ, σ, r) 세리프 표기"))
        btn_p_diag.clicked.connect(lambda: _append_hint("학술 개념도: 노드 박스와 연결 화살표, 핵심 키워드 중심의 구조화된 다이어그램"))

        preset_row1.addWidget(btn_p_econ)
        preset_row1.addWidget(btn_p_math)
        preset_row2.addWidget(btn_p_stat)
        preset_row2.addWidget(btn_p_diag)
        preset_grid.addLayout(preset_row1)
        preset_grid.addLayout(preset_row2)
        left_layout.addLayout(preset_grid)

        splitter.addWidget(left_widget)

        # --- Right Panel: Result & Preview ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        # Top Bar of Right Panel: Obsidian Theme Simulation Toggle
        theme_bar = QHBoxLayout()
        theme_lbl = QLabel("Obsidian 배경 시뮬레이션:")
        theme_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        theme_bar.addWidget(theme_lbl)

        self.btn_theme_dark = QPushButton("🌙 다크 테마 (#1e1e2e)")
        self.btn_theme_light = QPushButton("☀️ 라이트 테마 (#ffffff)")
        self.btn_theme_trans = QPushButton("🏁 슬레이트 (#2b2d42)")

        for tb in (self.btn_theme_dark, self.btn_theme_light, self.btn_theme_trans):
            tb.setCheckable(True)
            tb.setStyleSheet("""
                QPushButton {
                    font-size: 11px;
                    padding: 3px 8px;
                    background-color: #1f2937;
                    border: 1px solid #374151;
                    border-radius: 4px;
                }
                QPushButton:checked {
                    background-color: #2563eb;
                    color: #ffffff;
                    font-weight: bold;
                    border-color: #3b82f6;
                }
            """)

        self.btn_theme_dark.setChecked(True)
        self.btn_theme_dark.clicked.connect(lambda: self._set_preview_theme("dark"))
        self.btn_theme_light.clicked.connect(lambda: self._set_preview_theme("light"))
        self.btn_theme_trans.clicked.connect(lambda: self._set_preview_theme("slate"))

        theme_bar.addWidget(self.btn_theme_dark)
        theme_bar.addWidget(self.btn_theme_light)
        theme_bar.addWidget(self.btn_theme_trans)
        theme_bar.addStretch()
        right_layout.addLayout(theme_bar)

        self.tabs = QTabWidget()
        
        # Tab 1: SVG Visual Render
        self.svg_preview_scroll = QScrollArea()
        self.svg_preview_scroll.setWidgetResizable(True)
        self.svg_preview_scroll.setStyleSheet("background-color: #1e1e2e; border-radius: 8px;")
        
        self.svg_container = QWidget()
        self.svg_container.setStyleSheet("background-color: #1e1e2e;")
        self.svg_container_layout = QVBoxLayout(self.svg_container)
        self.svg_container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.svg_widget = QSvgWidget()
        self.svg_widget.setFixedSize(600, 420)
        self.svg_widget.setStyleSheet("background-color: #ffffff; border-radius: 4px;")
        self.svg_widget.hide()
        
        self.svg_placeholder = QLabel("변환된 SVG 그래픽이 여기에 렌더링됩니다.")
        self.svg_placeholder.setStyleSheet("color: #9ca3af; font-size: 14px;")
        self.svg_container_layout.addWidget(self.svg_placeholder)
        self.svg_container_layout.addWidget(self.svg_widget)
        self.svg_preview_scroll.setWidget(self.svg_container)

        self.tabs.addTab(self.svg_preview_scroll, "🎨 SVG 그래픽 미리보기")

        # Tab 2: Raw XML Code
        self.edit_svg_code = QPlainTextEdit()
        self.edit_svg_code.setReadOnly(True)
        self.edit_svg_code.setStyleSheet("font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 12px;")
        self.tabs.addTab(self.edit_svg_code, "📄 SVG XML 코드")

        # Tab 3: Real-time Conversion Log
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(6, 6, 6, 6)
        log_layout.setSpacing(6)

        log_actions = QHBoxLayout()
        self.btn_save_log = QPushButton("💾 로그 저장...")
        self.btn_save_log.setToolTip("현재 변환 로그를 텍스트 파일로 저장합니다")
        self.btn_save_log.clicked.connect(self._save_log)

        self.btn_copy_log = QPushButton("📋 로그 복사")
        self.btn_copy_log.setToolTip("로그 전문을 클립보드에 복사합니다")
        self.btn_copy_log.clicked.connect(self._copy_log)

        self.btn_open_log_folder = QPushButton("📂 로그 폴더 열기")
        self.btn_open_log_folder.setToolTip("자동 보존된 로그 파일 폴더를 Finder에서 엽니다")
        self.btn_open_log_folder.clicked.connect(self._open_log_folder)

        self.btn_clear_log = QPushButton("🗑️ 지우기")
        self.btn_clear_log.clicked.connect(self._clear_log)

        for b in (self.btn_save_log, self.btn_copy_log, self.btn_open_log_folder, self.btn_clear_log):
            b.setStyleSheet("""
                QPushButton {
                    font-size: 11px;
                    padding: 4px 8px;
                    background-color: #1f2937;
                    border: 1px solid #374151;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #374151; color: #60a5fa; }
            """)
            log_actions.addWidget(b)

        log_actions.addStretch()
        self.chk_auto_scroll = QCheckBox("자동 스크롤")
        self.chk_auto_scroll.setChecked(True)
        self.chk_auto_scroll.setStyleSheet("color: #9ca3af; font-size: 11px;")
        log_actions.addWidget(self.chk_auto_scroll)
        log_layout.addLayout(log_actions)

        self.edit_log = QPlainTextEdit()
        self.edit_log.setReadOnly(True)
        self.edit_log.setStyleSheet("""
            QPlainTextEdit {
                font-family: Menlo, Monaco, 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.45;
                background-color: #0b0f19;
                color: #38bdf8;
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        log_layout.addWidget(self.edit_log)
        self.tabs.addTab(log_panel, "📜 실시간 변환 로그")

        right_layout.addWidget(self.tabs)
        splitter.addWidget(right_widget)
        splitter.setSizes([460, 700])

        root_layout.addWidget(splitter, stretch=1)

        # 3. Bottom Action Bar
        bottom_bar = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        self.progress_bar.setMaximumWidth(200)
        bottom_bar.addWidget(self.progress_bar)

        self.lbl_status = QLabel("대기 중")
        self.lbl_status.setStyleSheet("color: #9ca3af; font-size: 13px;")
        bottom_bar.addWidget(self.lbl_status, stretch=1)

        self.btn_convert = QPushButton("⚡ SVG로 변환")
        self.btn_convert.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 8px 18px;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #1d4ed8; }
            QPushButton:disabled { background-color: #4b5563; }
        """)
        self.btn_convert.clicked.connect(self._start_conversion)
        self.btn_convert.setEnabled(False)

        self.btn_copy_xml = QPushButton("📋 SVG XML 복사")
        self.btn_copy_xml.clicked.connect(self._copy_svg_xml)
        self.btn_copy_xml.setEnabled(False)

        self.btn_copy_md = QPushButton("📝 Obsidian 서식 복사")
        self.btn_copy_md.setStyleSheet("""
            QPushButton {
                background-color: #047857;
                color: white;
                font-weight: 500;
                padding: 6px 12px;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #059669; }
            QPushButton:disabled { background-color: #4b5563; }
        """)
        self.btn_copy_md.clicked.connect(self._copy_obsidian_markdown)
        self.btn_copy_md.setEnabled(False)

        self.btn_save = QPushButton("💾 SVG 저장...")
        self.btn_save.clicked.connect(self._save_svg)
        self.btn_save.setEnabled(False)

        bottom_bar.addWidget(self.btn_convert)
        bottom_bar.addWidget(self.btn_copy_xml)
        bottom_bar.addWidget(self.btn_copy_md)
        bottom_bar.addWidget(self.btn_save)
        root_layout.addLayout(bottom_bar)

    def _setup_menu_bar(self):
        menu_bar = self.menuBar()

        # File Menu
        file_menu = menu_bar.addMenu("파일")
        act_open = file_menu.addAction("📂 이미지 열기...")
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._browse_image)

        act_save = file_menu.addAction("💾 SVG 저장...")
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self._save_svg)

        file_menu.addSeparator()
        act_save_log = file_menu.addAction("📜 로그 저장...")
        act_save_log.triggered.connect(self._save_log)

        act_open_log_folder = file_menu.addAction("📂 로그 폴더 열기")
        act_open_log_folder.triggered.connect(self._open_log_folder)

        # Edit Menu (Native ⌘V dispatch on macOS)
        edit_menu = menu_bar.addMenu("편집")
        act_paste = edit_menu.addAction("📋 이미지 붙여넣기")
        act_paste.setShortcut(QKeySequence.StandardKey.Paste)
        act_paste.triggered.connect(self._handle_paste_action)

        act_copy_xml = edit_menu.addAction("SVG XML 복사")
        act_copy_xml.triggered.connect(self._copy_svg_xml)

        act_copy_md = edit_menu.addAction("Obsidian 서식 복사")
        act_copy_md.triggered.connect(self._copy_obsidian_markdown)

        edit_menu.addSeparator()
        act_copy_log = edit_menu.addAction("변환 로그 복사")
        act_copy_log.triggered.connect(self._copy_log)

    def _setup_shortcuts(self):
        # Explicit Meta+V and Ctrl+V bindings for responsive macOS & cross-platform pasting
        self._sc_meta_v = QShortcut(QKeySequence("Meta+V"), self)
        self._sc_meta_v.activated.connect(self._handle_paste_action)

        self._sc_ctrl_v = QShortcut(QKeySequence("Ctrl+V"), self)
        self._sc_ctrl_v.activated.connect(self._handle_paste_action)

    def _handle_paste_action(self):
        focus = QApplication.focusWidget()
        cb = QApplication.clipboard()
        mime = cb.mimeData()
        # If user is editing context/hint text and copied pure text, let QPlainTextEdit paste text
        if isinstance(focus, QPlainTextEdit) and mime and mime.hasText() and not mime.hasImage():
            focus.paste()
            return
        self._paste_from_clipboard()

    def keyPressEvent(self, event: QKeyEvent):
        is_paste = event.matches(QKeySequence.StandardKey.Paste) or (
            event.key() == Qt.Key.Key_V
            and bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        )
        if is_paste:
            focus = QApplication.focusWidget()
            cb = QApplication.clipboard()
            mime = cb.mimeData()
            if isinstance(focus, QPlainTextEdit) and mime and mime.hasText() and not mime.hasImage():
                super().keyPressEvent(event)
                return
            if self._paste_from_clipboard():
                event.accept()
                return
        super().keyPressEvent(event)

    def _paste_from_clipboard(self) -> bool:
        cb = QApplication.clipboard()
        mime_data = cb.mimeData()
        if not mime_data:
            self.lbl_status.setText("⚠️ 클립보드가 비어 있습니다.")
            return False

        # 1. Direct Image in clipboard (Screenshot, copied image in browser/Preview)
        if mime_data.hasImage():
            image = cb.image()
            if not image.isNull():
                cache_dir = Path.home() / ".cache" / "svg_converter" / "clipboard"
                cache_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                target_path = cache_dir / f"pasted_image_{timestamp}.png"
                if image.save(str(target_path), "PNG"):
                    self._load_image(target_path)
                    self.lbl_file_info.setText(f"클립보드 이미지 ({image.width()}×{image.height()}px)")
                    self.lbl_status.setText(f"📋 클립보드에서 이미지를 붙여넣었습니다 ({image.width()}×{image.height()}px).")
                    return True

        # 2. File URLs in clipboard (e.g. copied image file in Finder)
        if mime_data.hasUrls():
            valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
            for url in mime_data.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.is_file() and p.suffix.lower() in valid_exts:
                        self._load_image(p)
                        self.lbl_status.setText(f"📋 클립보드에서 파일 붙여넣음: {p.name}")
                        return True

        # 3. Plain text containing an image file path
        if mime_data.hasText():
            text = mime_data.text().strip().strip("\"'")
            if text and "\n" not in text and len(text) < 1024:
                try:
                    p = Path(text).expanduser()
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
                        self._load_image(p)
                        self.lbl_status.setText(f"📋 클립보드 경로에서 파일 불러옴: {p.name}")
                        return True
                except Exception:
                    pass

        self.lbl_status.setText("⚠️ 클립보드에 이미지 또는 이미지 파일이 없습니다.")
        return False

    def _browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "변환할 그래프 이미지 선택",
            str(Path.home()),
            "이미지 파일 (*.png *.jpg *.jpeg *.webp)",
        )
        if path:
            self._load_image(Path(path))

    def _load_image(self, path: Path):
        self.current_image_path = path
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "파일 오류", f"이미지 파일을 열 수 없습니다: {path.name}")
            return

        target_size = self.drop_area.size()
        if target_size.width() <= 50 or target_size.height() <= 50:
            target_size = self.drop_area.sizeHint()
        if target_size.width() <= 50 or target_size.height() <= 50:
            target_size = self.size()

        # Render preview in drop area
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.drop_area.setPixmap(scaled)
        if path.name.startswith("pasted_image_"):
            self.lbl_file_info.setText(f"클립보드 이미지 ({pixmap.width()}×{pixmap.height()}px)")
        else:
            self.lbl_file_info.setText(f"{path.name} ({pixmap.width()}×{pixmap.height()}px)")
        self.btn_convert.setEnabled(True)
        self.lbl_status.setText(f"준비 완료: {path.name}")

    def _start_conversion(self):
        if not self.current_image_path:
            return

        self.btn_convert.setEnabled(False)
        self.btn_select_file.setEnabled(False)
        self.btn_paste.setEnabled(False)
        self.progress_bar.show()

        # Switch to real-time log tab so user can immediately monitor progress
        self.tabs.setCurrentIndex(2)

        self.start_timestamp = time.time()
        self.latest_log_msg = "Gemini AI 모델 호출 준비 중..."
        self.timer_stopwatch.start()

        self._append_log("=" * 64)
        self._append_log(f"🚀 SVG 변환 요청: {self.current_image_path.name}")
        self._append_log(f"   * AI 모델: {self.model_combo.currentText()}")
        ctx = self.edit_context.toPlainText().strip()
        if ctx:
            self._append_log(f"   * 맥락 텍스트: {ctx}")
        hint = self.edit_hint.toPlainText().strip()
        if hint:
            self._append_log(f"   * 추가 지침: {hint}")

        self.worker = ConversionWorker(
            image_path=self.current_image_path,
            context=ctx,
            hint=hint,
            model=self.model_combo.currentText(),
        )
        self.worker.log_signal.connect(self._append_log)
        self.worker.finished_signal.connect(self._on_conversion_finished)
        self.worker.error_signal.connect(self._on_conversion_error)
        self.worker.start()

    def _on_conversion_finished(self, res: SvgResult):
        self.timer_stopwatch.stop()
        total_time = time.time() - self.start_timestamp
        self.progress_bar.hide()
        self.btn_convert.setEnabled(True)
        self.btn_select_file.setEnabled(True)
        self.btn_paste.setEnabled(True)

        if not res.is_graph:
            self._append_log(f"⚠️ [완료 {total_time:.2f}초] 2차원 좌표축 그래프/다이어그램 미감지 (NO_GRAPH)")
            self.lbl_status.setText(f"⚠️ 감지 실패: 2차원 좌표축 그래프가 아닙니다 (총 {total_time:.1f}초)")
            self.tabs.setCurrentIndex(2)
            QMessageBox.information(
                self,
                "변환 결과",
                "해당 이미지는 2차원 좌표축 그래프나 수식 다이어그램으로 인식되지 않았습니다 (NO_GRAPH).\n\n"
                "맥락 설명(Context) 란에 도표의 내용과 변수들을 입력하고 다시 시도해보세요.\n\n"
                "[📜 실시간 변환 로그] 탭에서 상세 로그를 확인할 수 있습니다.",
            )
            return

        if res.error or not res.svg:
            self._append_log(f"❌ [실패 {total_time:.2f}초] 오류: {res.error}")
            self.lbl_status.setText(f"❌ 변환 실패 (총 {total_time:.1f}초): {res.error}")
            self.tabs.setCurrentIndex(2)
            QMessageBox.critical(self, "변환 실패", f"SVG 생성 중 오류가 발생했습니다:\n{res.error}\n\n[📜 실시간 변환 로그] 탭을 확인하세요.")
            return

        self.current_svg = res.svg
        self.edit_svg_code.setPlainText(res.svg)

        # Render SVG in QSvgWidget
        svg_bytes = QByteArray(res.svg.encode("utf-8"))
        self.svg_widget.load(svg_bytes)
        self.svg_placeholder.hide()
        self.svg_widget.show()

        self.btn_copy_xml.setEnabled(True)
        self.btn_copy_md.setEnabled(True)
        self.btn_save.setEnabled(True)

        self._append_log(f"✅ [성공 {total_time:.2f}초] SVG 렌더링 완료 (XML 크기: {len(res.svg):,}자)")
        self.lbl_status.setText(f"✅ SVG 변환 완료! (총 {total_time:.1f}초 소요)")
        # Automatically switch to Preview tab upon success!
        self.tabs.setCurrentIndex(0)

    def _on_conversion_error(self, err_msg: str):
        self.timer_stopwatch.stop()
        total_time = time.time() - self.start_timestamp
        self.progress_bar.hide()
        self.btn_convert.setEnabled(True)
        self.btn_select_file.setEnabled(True)
        self.btn_paste.setEnabled(True)
        self._append_log(f"❌ [오류 {total_time:.2f}초] 예외 발생: {err_msg}")
        self.lbl_status.setText(f"❌ 오류 발생: {err_msg}")
        self.tabs.setCurrentIndex(2)
        QMessageBox.critical(self, "변환 오류", f"처리 중 예외가 발생했습니다:\n{err_msg}\n\n[📜 실시간 변환 로그] 탭을 확인하세요.")

    def _update_stopwatch(self):
        elapsed = time.time() - self.start_timestamp
        summary = self.latest_log_msg or "AI 모델 처리 중..."
        if len(summary) > 50:
            summary = summary[:47] + "..."
        self.lbl_status.setText(f"⏳ [변환 진행 중 ⏱️ {elapsed:.1f}초] {summary}")

    def _append_log(self, msg: str):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{now_str}] {msg}"
        self.latest_log_msg = msg
        self.edit_log.appendPlainText(line)
        if hasattr(self, "chk_auto_scroll") and self.chk_auto_scroll.isChecked():
            self.edit_log.verticalScrollBar().setValue(self.edit_log.verticalScrollBar().maximum())
        self._write_persistent_log(line)

    def _persistent_log_dir(self) -> Path:
        """Resolve only; append/open-folder own directory materialization."""
        if self._log_dir is not None:
            return self._log_dir
        return Path.home() / ".cache" / "svg_converter" / "logs"

    def _write_persistent_log(self, line: str):
        try:
            log_dir = self._persistent_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            log_file = log_dir / f"svg_converter_{today_str}.log"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {line}\n")
        except Exception:
            pass

    def _save_log(self):
        log_text = self.edit_log.toPlainText().strip()
        if not log_text:
            QMessageBox.information(self, "로그 저장", "저장할 로그 내용이 없습니다.")
            return

        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"svg_converter_log_{now_str}.txt"
        default_dir = Path.home() / "Downloads" if (Path.home() / "Downloads").exists() else Path.home()
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "로그 파일로 저장",
            str(default_dir / default_name),
            "텍스트 로그 (*.txt *.log)",
        )
        if dest:
            dest_path = Path(dest)
            dest_path.write_text(log_text, encoding="utf-8")
            self.lbl_status.setText(f"💾 로그 저장 완료: {dest_path.name}")
            QMessageBox.information(self, "저장 완료", f"로그가 파일로 저장되었습니다:\n{dest}")

    def _copy_log(self):
        log_text = self.edit_log.toPlainText()
        if not log_text:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(log_text)
        self.lbl_status.setText("📋 전체 변환 로그가 클립보드에 복사되었습니다.")

    def _open_log_folder(self):
        log_dir = self._persistent_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))
        self.lbl_status.setText(f"📂 로그 폴더 열기: {log_dir}")

    def _clear_log(self):
        self.edit_log.clear()
        self.lbl_status.setText("로그가 초기화되었습니다.")

    def _set_preview_theme(self, mode: str):
        self.btn_theme_dark.setChecked(mode == "dark")
        self.btn_theme_light.setChecked(mode == "light")
        self.btn_theme_trans.setChecked(mode == "slate")

        if mode == "dark":
            bg_color = "#1e1e2e"
        elif mode == "light":
            bg_color = "#f8fafc"
        else:
            bg_color = "#2b2d42"

        self.svg_preview_scroll.setStyleSheet(f"background-color: {bg_color}; border-radius: 8px;")
        self.svg_container.setStyleSheet(f"background-color: {bg_color};")

    def _copy_svg_xml(self):
        if not self.current_svg:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self.current_svg)
        self.lbl_status.setText("📋 클립보드에 순수 SVG XML 코드가 복사되었습니다.")

    def _copy_obsidian_markdown(self):
        if not self.current_svg:
            return

        # If file was loaded from or saved in an Obsidian assets folder
        if self.current_image_path and self.current_image_path.parent.name == "assets":
            svg_stem = self.current_image_path.stem
            if svg_stem.startswith("figure-slide-"):
                svg_stem = svg_stem.replace("figure-slide-", "graph-slide-")
            md_text = f"> [!NOTE] Graph\n> ![[assets/{svg_stem}.svg]]\n"
        else:
            md_lines = [f"> {line}" for line in self.current_svg.splitlines()]
            md_text = "> [!NOTE] Graph\n> ```xml\n" + "\n".join(md_lines) + "\n> ```\n"

        clipboard = QApplication.clipboard()
        clipboard.setText(md_text)
        self.lbl_status.setText("📝 Obsidian 콜아웃 마크다운이 클립보드에 복사되었습니다.")

    def _save_svg(self):
        if not self.current_svg or not self.current_image_path:
            return

        # Smart default naming: figure-slide-0004 -> graph-slide-0004, pasted_image -> graph
        stem = self.current_image_path.stem
        if stem.startswith("figure-slide-"):
            default_name = stem.replace("figure-slide-", "graph-slide-") + ".svg"
        elif stem.startswith("pasted_image_"):
            default_name = stem.replace("pasted_image_", "graph_") + ".svg"
        elif stem.startswith("pasted_image"):
            default_name = "graph.svg"
        else:
            default_name = f"{stem}.svg"

        save_dir = self.current_image_path.parent
        if ".cache" in str(save_dir) or "tmp" in str(save_dir).lower():
            if (Path.home() / "Downloads").exists():
                save_dir = Path.home() / "Downloads"
            elif (Path.home() / "Desktop").exists():
                save_dir = Path.home() / "Desktop"
            else:
                save_dir = Path.home()

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "SVG 파일로 저장",
            str(save_dir / default_name),
            "SVG 벡터 이미지 (*.svg)",
        )
        if dest:
            dest_path = Path(dest)
            dest_path.write_text(self.current_svg, encoding="utf-8")
            self.lbl_status.setText(f"💾 저장 완료: {dest_path.name}")
            QMessageBox.information(
                self,
                "저장 완료",
                f"성공적으로 저장되었습니다:\n{dest}\n\n[📝 Obsidian 서식 복사] 버튼을 누르면 노트에 바로 붙여넣을 수 있습니다.",
            )

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #111827; }
            QWidget { color: #f3f4f6; }
            QPlainTextEdit {
                background-color: #1f2937;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 6px;
            }
            QPlainTextEdit:focus { border: 1px solid #3b82f6; }
            QComboBox {
                background-color: #1f2937;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton {
                background-color: #374151;
                color: #f3f4f6;
                border: 1px solid #4b5563;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #4b5563; }
            QTabWidget::pane {
                border: 1px solid #374151;
                border-radius: 6px;
                background-color: #1f2937;
            }
            QTabBar::tab {
                background: #111827;
                color: #9ca3af;
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border: 1px solid #374151;
                border-bottom: none;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #1f2937;
                color: #ffffff;
                font-weight: bold;
            }
        """)


def run_gui(initial_image: Optional[Path] = None) -> int:
    from _scripts_2.apps.branding import apply_qt_branding

    app = QApplication.instance() or QApplication(sys.argv)
    apply_qt_branding(app, "svg-converter")
    app.setStyle("Fusion")
    win = SvgConverterWindow(initial_image=initial_image)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
