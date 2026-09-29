from __future__ import annotations

import hashlib
import json
import re
import sys
import ctypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from core import (
    AUDIO_EXTENSIONS,
    AnnotationNote,
    Attempt,
    Caption,
    HighlightNote,
    MEDIA_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    compare_words,
    export_docx,
    export_txt,
    explain_spelling_difference,
    extract_embedded_subtitle,
    find_matching_subtitle,
    format_ms,
    list_library_files,
    parse_subtitle,
    retime_plain_text,
    repair_external_only_captions,
    synchronize_with_embedded,
    tokenize,
    detect_subtitle_offset,
)


APP_VERSION = "8.0.0"
APP_NAME = "Shadowing 拼写练习 V8"


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


class WordTrackWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.expected_words: list[str] = []
        self.typed_words: list[str] = []
        self.revealed = False
        self.hint_mode = "off"
        self.hint_indices: set[int] = set()
        self.setMinimumHeight(96)

    def set_sentence(self, sentence: str) -> None:
        self.expected_words = tokenize(sentence)
        self.typed_words = []
        self.revealed = False
        self._choose_hint_indices()
        self._update_height()
        self.update()

    def set_hint_mode(self, mode: str) -> None:
        self.hint_mode = mode
        self._choose_hint_indices()
        self.update()

    def _choose_hint_indices(self) -> None:
        self.hint_indices.clear()
        if self.hint_mode == "off" or not self.expected_words:
            return
        stopwords = {
            "a", "an", "the", "is", "am", "are", "was", "were", "be", "been",
            "i", "you", "he", "she", "it", "we", "they", "to", "of", "in", "on",
            "at", "for", "and", "or", "but", "that", "this", "do", "did", "does",
        }
        ranked = sorted(
            range(len(self.expected_words)),
            key=lambda index: (
                self.expected_words[index].casefold() not in stopwords,
                len(self.expected_words[index]),
                -abs(index - len(self.expected_words) / 2),
            ),
            reverse=True,
        )
        count = 1 if self.hint_mode == "word1" else 2
        self.hint_indices = set(ranked[:count])

    def set_typed(self, text: str) -> None:
        self.typed_words = tokenize(text)
        self.update()

    def reveal(self, value: bool = True) -> None:
        self.revealed = value
        self.update()

    def _word_width(self, word: str) -> int:
        return min(150, max(34, 18 + len(word) * 9))

    def _layout_items(self) -> list[tuple[int, QRect]]:
        x, y = 12, 8
        row_height = 72
        available = max(200, self.width() - 24)
        result: list[tuple[int, QRect]] = []
        for i, word in enumerate(self.expected_words):
            width = self._word_width(word)
            if x + width > available and x > 12:
                x = 12
                y += row_height
            result.append((i, QRect(x, y, width, 32)))
            x += width + 12
        return result

    def _update_height(self) -> None:
        QTimer.singleShot(0, self._apply_height)

    def _apply_height(self) -> None:
        items = self._layout_items()
        height = max(88, (items[-1][1].bottom() + 42) if items else 88)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_height()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Microsoft YaHei UI", 10)
        painter.setFont(font)
        for i, rect in self._layout_items():
            typed = self.typed_words[i] if i < len(self.typed_words) else ""
            expected = self.expected_words[i]
            ok = bool(typed) and typed.casefold().replace("’", "'") == expected.casefold().replace("’", "'")
            if typed:
                color = QColor("#35d07f") if ok else QColor("#ff6b6b")
                if not ok:
                    painter.setPen(QPen(color, 2))
                    painter.setBrush(QColor(255, 107, 107, 20))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
                painter.setPen(color)
                painter.drawText(rect, Qt.AlignCenter, typed)
            else:
                color = QColor("#687086")
                if not self.revealed and i in self.hint_indices:
                    hint = expected if self.hint_mode.startswith("word") else expected[:1].upper()
                    painter.setPen(QColor("#78a9ff"))
                    painter.drawText(rect, Qt.AlignCenter, hint)
            painter.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            if self.revealed:
                correction_rect = QRect(rect.left(), rect.bottom() + 5, rect.width(), 25)
                if not typed:
                    correction = expected
                elif not ok:
                    correction = f"↓ {expected}"
                else:
                    correction = ""
                if correction:
                    painter.setPen(QColor("#ffd166"))
                    painter.drawText(correction_rect, Qt.AlignHCenter | Qt.AlignTop, correction)


class DropFrame(QFrame):
    fileDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.fileDropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1380, 920)
        self.setMinimumSize(1000, 720)
        self.setAcceptDrops(True)

        self.media_path: Path | None = None
        self.subtitle_path: Path | None = None
        self.captions: list[Caption] = []
        self.current_index = 0
        self.attempts: list[Attempt] = []
        self.highlight_notes: list[HighlightNote] = []
        self.annotation_notes: list[AnnotationNote] = []
        self.skipped_texts: set[str] = set()
        self.plain_text_timing = False
        self.segment_playing = False
        self.slider_dragging = False
        self.loop_request_id = 0
        self.subtitle_offset_ms = 0
        self.lead_padding_ms = 700
        self.tail_padding_ms = 800
        self.episode_paths: list[Path] = []
        self.library_root: Path | None = None
        self.proper_nouns: set[str] = set()
        self.synced_with_embedded = False
        self.settings = QSettings("OpenAI", "ShadowingTrainer")

        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)

        self.preview_audio = QAudioOutput(self)
        self.preview_audio.setMuted(True)
        self.preview_player = QMediaPlayer(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_sink = QVideoSink(self)
        self.preview_player.setVideoSink(self.preview_sink)
        self.preview_sink.videoFrameChanged.connect(self.thumbnail_frame_changed)
        self.preview_position_ms = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(90)
        self.preview_timer.timeout.connect(self.request_thumbnail_frame)

        self._build_ui()
        self._build_thumbnail_popup()
        self._connect_player()
        self._setup_shortcuts()
        self._apply_theme()
        last_folder = self.settings.value("last_folder", "", str)
        if last_folder and Path(last_folder).is_dir():
            self.populate_episode_sidebar(Path(last_folder))

    def _build_ui(self) -> None:
        root = DropFrame()
        root.fileDropped.connect(self.handle_dropped_path)
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(7)

        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        outer.addWidget(main, 1)

        top = QHBoxLayout()
        icon_path = resource_path("assets/shadowing_icon.png")
        if icon_path.exists():
            logo = QLabel()
            logo.setPixmap(QPixmap(str(icon_path)).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setFixedSize(42, 42)
            logo.setAlignment(Qt.AlignCenter)
            top.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        for text, slot, accent in (
            ("导入整个文件夹", self.choose_folder, True),
            ("打开单个视频 / 音频", self.choose_media, False),
            ("打开字幕", self.choose_subtitle, False),
            ("读取内嵌字幕", self.load_embedded_subtitle, False),
        ):
            button = QPushButton(text)
            if accent:
                button.setObjectName("accentButton")
            button.clicked.connect(slot)
            top.addWidget(button)
        layout.addLayout(top)

        video_panel = QWidget()
        video_layout = QVBoxLayout(video_panel)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(6)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(180)
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        self.player.setVideoOutput(self.video)
        video_layout.addWidget(self.video, 1)

        self.audio_panel = QLabel("♫\n音频模式")
        self.audio_panel.setObjectName("audioPanel")
        self.audio_panel.setAlignment(Qt.AlignCenter)
        self.audio_panel.setWordWrap(True)
        self.audio_panel.setMinimumHeight(180)
        self.audio_panel.hide()
        video_layout.addWidget(self.audio_panel, 1)

        self.subtitle_line = QLabel("")
        self.subtitle_line.setObjectName("subtitleLine")
        self.subtitle_line.setAlignment(Qt.AlignCenter)
        self.subtitle_line.setWordWrap(True)
        self.subtitle_line.setMinimumHeight(42)
        self.subtitle_line.hide()
        video_layout.addWidget(self.subtitle_line)

        transport = QHBoxLayout()
        self.play_button = QPushButton("播放本句")
        self.play_button.setObjectName("accentButton")
        self.play_button.clicked.connect(self.play_current_segment)
        transport.addWidget(self.play_button)
        self.normal_play_button = QPushButton("连续播放")
        self.normal_play_button.clicked.connect(self.play_normal)
        transport.addWidget(self.normal_play_button)
        repeat_button = QPushButton("重播")
        repeat_button.clicked.connect(self.play_current_segment)
        transport.addWidget(repeat_button)
        self.loop_checkbox = QCheckBox("单句循环")
        self.loop_checkbox.toggled.connect(self.loop_setting_changed)
        transport.addWidget(self.loop_checkbox)
        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(self.time_label)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(self.seek_pressed)
        self.seek.sliderMoved.connect(self.seek_preview_moved)
        self.seek.sliderReleased.connect(self.seek_released)
        transport.addWidget(self.seek, 1)
        video_layout.addLayout(transport)

        options = QHBoxLayout()
        options.addWidget(QLabel("速度"))
        self.speed = QComboBox()
        self.speed.addItems(["0.5×", "0.75×", "0.8×", "0.9×", "1.0×", "1.1×", "1.25×", "1.5×"])
        self.speed.setCurrentText("1.0×")
        self.speed.currentTextChanged.connect(lambda text: self.player.setPlaybackRate(float(text[:-1])))
        options.addWidget(self.speed)
        self.subtitle_toggle = QCheckBox("显示字幕")
        self.subtitle_toggle.toggled.connect(self.update_subtitle_overlay)
        options.addWidget(self.subtitle_toggle)
        options.addSpacing(10)
        options.addWidget(QLabel("字幕校准"))
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-10.0, 10.0)
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSingleStep(0.1)
        self.offset_spin.setSuffix(" 秒")
        self.offset_spin.setToolTip("正数让字幕和单句播放往后移，负数往前移")
        self.offset_spin.setValue(float(self.settings.value("subtitle_offset_seconds", 0.0)))
        self.subtitle_offset_ms = int(self.offset_spin.value() * 1000)
        self.offset_spin.valueChanged.connect(self.subtitle_offset_changed)
        options.addWidget(self.offset_spin)
        auto_align = QPushButton("自动对齐")
        auto_align.setToolTip("用媒体内嵌英文字幕校准外部字幕")
        auto_align.clicked.connect(self.auto_align_subtitles)
        options.addWidget(auto_align)
        options.addWidget(QLabel("句首提前"))
        self.lead_spin = QDoubleSpinBox()
        self.lead_spin.setRange(0.0, 3.0)
        self.lead_spin.setDecimals(1)
        self.lead_spin.setSingleStep(0.1)
        self.lead_spin.setSuffix(" 秒")
        self.lead_spin.setValue(float(self.settings.value("lead_padding_seconds", 0.7)))
        self.lead_padding_ms = int(self.lead_spin.value() * 1000)
        self.lead_spin.valueChanged.connect(self.lead_padding_changed)
        options.addWidget(self.lead_spin)
        options.addWidget(QLabel("句尾延长"))
        self.tail_spin = QDoubleSpinBox()
        self.tail_spin.setRange(0.0, 3.0)
        self.tail_spin.setDecimals(1)
        self.tail_spin.setSingleStep(0.1)
        self.tail_spin.setSuffix(" 秒")
        self.tail_spin.setValue(float(self.settings.value("tail_padding_seconds", 0.8)))
        self.tail_padding_ms = int(self.tail_spin.value() * 1000)
        self.tail_spin.valueChanged.connect(self.tail_padding_changed)
        options.addWidget(self.tail_spin)
        options.addStretch()
        options.addWidget(QLabel("拖动下方分隔条可调整画面大小"))
        video_layout.addLayout(options)

        practice = QFrame()
        practice.setObjectName("practiceCard")
        practice_layout = QVBoxLayout(practice)
        practice_layout.setContentsMargins(18, 14, 18, 14)
        header = QHBoxLayout()
        self.counter = QLabel("尚未载入字幕")
        self.counter.setObjectName("sectionTitle")
        header.addWidget(self.counter)
        self.sentence_combo = QComboBox()
        self.sentence_combo.setMinimumWidth(230)
        self.sentence_combo.setMaxVisibleItems(22)
        self.sentence_combo.setToolTip("展开后可滚动选择任意一句")
        self.sentence_combo.activated.connect(self.jump_to_caption)
        header.addWidget(self.sentence_combo)
        header.addSpacing(12)
        header.addWidget(QLabel("提示"))
        self.hint_combo = QComboBox()
        self.hint_combo.addItem("关闭", "off")
        self.hint_combo.addItem("两个首字母", "initial2")
        self.hint_combo.addItem("一个关键词", "word1")
        self.hint_combo.addItem("两个关键词", "word2")
        saved_hint = self.settings.value("hint_mode", "off", str)
        hint_index = max(0, self.hint_combo.findData(saved_hint))
        self.hint_combo.setCurrentIndex(hint_index)
        self.hint_combo.currentIndexChanged.connect(self.hint_mode_changed)
        header.addWidget(self.hint_combo)
        header.addStretch()
        self.score_label = QLabel("拖入视频或音频即可开始")
        self.score_label.setObjectName("muted")
        header.addWidget(self.score_label)
        practice_layout.addLayout(header)

        meta = QHBoxLayout()
        self.speaker_label = QLabel("说话人：字幕未标注")
        self.speaker_label.setObjectName("muted")
        meta.addWidget(self.speaker_label)
        meta.addStretch()
        self.timing_warning = QLabel("")
        self.timing_warning.setObjectName("warning")
        meta.addWidget(self.timing_warning)
        practice_layout.addLayout(meta)

        self.word_track = WordTrackWidget()
        self.word_track.set_hint_mode(saved_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(90)
        scroll.setWidget(self.word_track)
        scroll.setMaximumHeight(170)
        practice_layout.addWidget(scroll)

        self.input = QLineEdit()
        self.input.setPlaceholderText("听完后，在这里输入你听到的句子……")
        self.input.setClearButtonEnabled(True)
        self.input.setMinimumHeight(46)
        self.input.textChanged.connect(self.word_track.set_typed)
        self.input.returnPressed.connect(self.handle_enter)
        practice_layout.addWidget(self.input)

        self.answer_label = QLabel("")
        self.answer_label.setWordWrap(True)
        self.answer_label.setObjectName("answer")
        practice_layout.addWidget(self.answer_label)

        self.original_panel = QWidget()
        original_layout = QVBoxLayout(self.original_panel)
        original_layout.setContentsMargins(0, 0, 0, 0)
        original_layout.setSpacing(5)
        original_header = QHBoxLayout()
        original_title = QLabel("原句（拖动选择词或短语，右键荧光高亮并加入笔记）")
        original_title.setObjectName("muted")
        original_header.addWidget(original_title)
        original_header.addStretch()
        original_layout.addLayout(original_header)
        self.original_text = QTextEdit()
        self.original_text.setObjectName("originalSentence")
        self.original_text.setReadOnly(True)
        self.original_text.setAcceptRichText(False)
        self.original_text.setMinimumHeight(72)
        self.original_text.setMaximumHeight(104)
        self.original_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.original_text.customContextMenuRequested.connect(self.show_original_context_menu)
        original_layout.addWidget(self.original_text)
        self.original_panel.hide()
        practice_layout.addWidget(self.original_panel)

        self.translation_label = QLabel("")
        self.translation_label.setWordWrap(True)
        self.translation_label.setObjectName("translation")
        self.translation_label.hide()
        practice_layout.addWidget(self.translation_label)

        actions = QHBoxLayout()
        previous = QPushButton("← 上一句")
        previous.clicked.connect(lambda: self.move_caption(-1))
        actions.addWidget(previous)
        submit = QPushButton("提交并判词  Enter")
        submit.setObjectName("accentButton")
        submit.clicked.connect(self.submit_answer)
        actions.addWidget(submit)
        self.answer_button = QPushButton("显示答案")
        self.answer_button.clicked.connect(self.toggle_answer)
        actions.addWidget(self.answer_button)
        next_button = QPushButton("下一句并播放 →")
        next_button.clicked.connect(lambda: self.move_caption(1, play=True))
        actions.addWidget(next_button)
        merge_button = QPushButton("与下一句合并")
        merge_button.setToolTip("当字幕把一句话切碎时，手动合并当前句和下一句")
        merge_button.clicked.connect(self.merge_with_next)
        actions.addWidget(merge_button)
        self.skip_button = QPushButton("不重要，跳过")
        self.skip_button.clicked.connect(self.toggle_skip_current)
        actions.addWidget(self.skip_button)
        actions.addStretch()
        export_txt_button = QPushButton("导出 TXT / 笔记")
        export_txt_button.clicked.connect(lambda: self.export_report("txt"))
        actions.addWidget(export_txt_button)
        export_word_button = QPushButton("导出 Word")
        export_word_button.clicked.connect(lambda: self.export_report("docx"))
        actions.addWidget(export_word_button)
        practice_layout.addLayout(actions)

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(video_panel)
        self.main_splitter.addWidget(practice)
        self.main_splitter.setStretchFactor(0, 7)
        self.main_splitter.setStretchFactor(1, 3)
        self.main_splitter.setSizes([625, 245])
        layout.addWidget(self.main_splitter, 1)

        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("sidebarHandle")
        self.sidebar_toggle.setText(">")
        self.sidebar_toggle.setToolTip("收起文件夹树")
        self.sidebar_toggle.setFixedWidth(26)
        self.sidebar_toggle.clicked.connect(self.toggle_episode_sidebar)
        outer.addWidget(self.sidebar_toggle, 0, Qt.AlignVCenter)

        self.episode_sidebar = QFrame()
        self.episode_sidebar.setObjectName("episodeSidebar")
        self.episode_sidebar.setMinimumWidth(220)
        self.episode_sidebar.setMaximumWidth(310)
        side_layout = QVBoxLayout(self.episode_sidebar)
        side_header = QLabel("文件夹内容")
        side_header.setObjectName("sectionTitle")
        side_layout.addWidget(side_header)
        self.folder_label = QLabel("尚未选择文件夹")
        self.folder_label.setObjectName("muted")
        self.folder_label.setWordWrap(True)
        side_layout.addWidget(self.folder_label)
        self.episode_list = QTreeWidget()
        self.episode_list.setHeaderHidden(True)
        self.episode_list.setAnimated(True)
        self.episode_list.setIndentation(18)
        self.episode_list.itemClicked.connect(self.episode_selected)
        side_layout.addWidget(self.episode_list, 1)
        outer.addWidget(self.episode_sidebar)

        self.statusBar().showMessage("支持选择整个文件夹，递归读取视频、音频、SRT、LRC 和 TXT")

    def _build_thumbnail_popup(self) -> None:
        self.thumbnail_popup = QFrame(self, Qt.ToolTip | Qt.FramelessWindowHint)
        self.thumbnail_popup.setObjectName("thumbnailPopup")
        self.thumbnail_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        popup_layout = QVBoxLayout(self.thumbnail_popup)
        popup_layout.setContentsMargins(7, 7, 7, 6)
        popup_layout.setSpacing(4)
        self.thumbnail_image = QLabel("拖动进度条预览")
        self.thumbnail_image.setObjectName("thumbnailImage")
        self.thumbnail_image.setAlignment(Qt.AlignCenter)
        self.thumbnail_image.setFixedSize(224, 126)
        popup_layout.addWidget(self.thumbnail_image)
        self.thumbnail_time = QLabel("00:00")
        self.thumbnail_time.setAlignment(Qt.AlignCenter)
        self.thumbnail_time.setObjectName("thumbnailTime")
        popup_layout.addWidget(self.thumbnail_time)
        self.thumbnail_popup.adjustSize()
        self.thumbnail_popup.hide()

    def _connect_player(self) -> None:
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        self.player.errorOccurred.connect(self.media_error)

    def _setup_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+O", self.choose_media),
            ("Ctrl+R", self.play_current_segment),
            ("Alt+Right", lambda: self.move_caption(1, play=True)),
            ("Alt+Left", lambda: self.move_caption(-1)),
            ("Ctrl+E", lambda: self.export_report("txt")),
        ]
        for sequence, slot in shortcuts:
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(slot)
            self.addAction(action)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #11141c; color: #e9edf7; font-family: 'Microsoft YaHei UI'; font-size: 14px; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #ffffff; }
            QLabel#sectionTitle { font-size: 16px; font-weight: 700; }
            QLabel#muted { color: #9aa3b8; }
            QLabel#warning { color: #ffbf69; }
            QLabel#answer { color: #b9c4dc; padding: 4px 2px; }
            QLabel#translation { color: #f4d06f; background: #121722; border-left: 3px solid #d9ad45; padding: 7px 10px; }
            QLabel#subtitleLine { background: #07090e; color: #ffffff; padding: 8px 14px; border-radius: 6px; font-size: 19px; font-weight: 600; }
            QLabel#audioPanel { background: #080b12; border: 1px solid #252d3d; border-radius: 10px; color: #cbd4ea; font-size: 24px; font-weight: 600; padding: 30px; }
            QFrame#thumbnailPopup { background: #0b0e15; border: 1px solid #59647c; border-radius: 8px; }
            QLabel#thumbnailImage { background: #050608; color: #8d97ad; border-radius: 5px; }
            QLabel#thumbnailTime { color: #ffffff; font-weight: 700; }
            QTextEdit#originalSentence { background: #111722; border: 1px solid #38435a; border-radius: 8px; padding: 8px 10px; color: #edf1fa; font-size: 16px; selection-background-color: #7180ff; }
            QVideoWidget { background: #050608; border-radius: 10px; }
            QFrame#practiceCard { background: #1a1f2b; border: 1px solid #2b3344; border-radius: 12px; }
            QFrame#episodeSidebar { background: #171c27; border: 1px solid #2b3344; border-radius: 10px; }
            QPushButton { background: #272e3e; border: 1px solid #39445a; border-radius: 7px; padding: 8px 13px; }
            QPushButton:hover { background: #313a4e; }
            QPushButton:pressed { background: #202635; }
            QPushButton#accentButton { background: #5b6df8; border-color: #7180ff; color: white; font-weight: 600; }
            QPushButton#accentButton:hover { background: #6a7bff; }
            QLineEdit { background: #10141d; border: 1px solid #3a445b; border-radius: 8px; padding: 8px 12px; font-size: 17px; selection-background-color: #5b6df8; }
            QLineEdit:focus { border: 2px solid #6879ff; }
            QComboBox, QDoubleSpinBox { background: #272e3e; border: 1px solid #39445a; border-radius: 6px; padding: 5px 9px; }
            QTreeWidget { background: #10141d; border: 1px solid #313a4e; border-radius: 7px; padding: 5px; }
            QTreeWidget::item { padding: 6px 3px; border-radius: 5px; }
            QTreeWidget::item:selected { background: #4f61d8; color: white; }
            QToolButton#sidebarHandle { background: #293147; border: 1px solid #3b4660; border-radius: 6px; padding: 10px 2px; }
            QSplitter::handle:vertical { background: #3a4661; height: 8px; margin: 2px 100px; border-radius: 4px; }
            QSlider::groove:horizontal { height: 5px; background: #343d51; border-radius: 2px; }
            QSlider::handle:horizontal { width: 15px; margin: -5px 0; background: #7180ff; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #5b6df8; }
            QCheckBox { spacing: 7px; }
            QScrollArea { background: transparent; }
            QStatusBar { color: #98a2b8; }
            """
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.handle_dropped_path(urls[0].toLocalFile())

    def handle_dropped_path(self, path_text: str) -> None:
        path = Path(path_text)
        if path.is_dir():
            self.load_folder(path)
        elif path.suffix.lower() in MEDIA_EXTENSIONS:
            self.load_media(path)
        elif path.suffix.lower() in SUBTITLE_EXTENSIONS:
            self.load_subtitle(path)
        else:
            QMessageBox.information(self, APP_NAME, "暂不支持这个文件格式。")

    def choose_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频或音频",
            self.settings.value("last_folder", "", str),
            "媒体文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv *.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus *.wma *.aiff *.aif *.ac3 *.mka *.mp2 *.amr *.m4b *.oga *.ape *.caf);;"
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv);;"
            "音频文件 (*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus *.wma *.aiff *.aif *.ac3 *.mka *.mp2 *.amr *.m4b *.oga *.ape *.caf);;"
            "所有文件 (*.*)",
        )
        if path:
            self.load_media(Path(path))

    def choose_subtitle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择字幕或文本",
            str(self.media_path.parent) if self.media_path else "",
            "字幕文件 (*.srt *.ass *.ssa *.vtt *.lrc *.txt);;所有文件 (*.*)",
        )
        if path:
            self.load_subtitle(Path(path))

    def choose_folder(self) -> None:
        initial = Path(self.settings.value("last_folder", "", str))
        if initial.is_file():
            initial = initial.parent
        if not initial.is_dir():
            initial = Path.home()
        dialog = QFileDialog(self, "导入整个文件夹", str(initial))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setLabelText(QFileDialog.DialogLabel.Accept, "导入这个文件夹")
        dialog.setLabelText(QFileDialog.DialogLabel.Reject, "取消")
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected and Path(selected[0]).is_dir():
                self.load_folder(Path(selected[0]))

    def _episode_label(self, path: Path) -> str:
        match = re.search(r"(?i)(S\d{1,2}E\d{1,2})(?:[. _-]+([^.[\]]+))?", path.stem)
        if match:
            episode = match.group(1).upper()
            title = (match.group(2) or "").replace("_", " ").strip()
            return f"{episode}  {title}".strip()
        return path.stem

    def populate_episode_sidebar(self, folder: Path, selected: Path | None = None) -> list[Path]:
        folder = folder.resolve()
        library_files = list_library_files(folder)
        media_files = [path for path in library_files if path.suffix.lower() in MEDIA_EXTENSIONS]
        self.library_root = folder
        self.episode_paths = media_files
        self.episode_list.clear()
        root_item = QTreeWidgetItem([folder.name])
        root_item.setToolTip(0, str(folder))
        self.episode_list.addTopLevelItem(root_item)
        directory_items: dict[Path, QTreeWidgetItem] = {folder: root_item}
        selected_item: QTreeWidgetItem | None = None
        for path in library_files:
            parent_item = root_item
            current = folder
            for part in path.relative_to(folder).parts[:-1]:
                current = current / part
                if current not in directory_items:
                    directory_item = QTreeWidgetItem([part])
                    directory_item.setToolTip(0, str(current))
                    parent_item.addChild(directory_item)
                    directory_items[current] = directory_item
                parent_item = directory_items[current]
            extension = path.suffix.lower()
            if extension in VIDEO_EXTENSIONS:
                prefix = "▶ "
            elif extension in AUDIO_EXTENSIONS:
                prefix = "♫ "
            elif extension == ".txt":
                prefix = "TXT "
            elif extension == ".lrc":
                prefix = "LRC "
            else:
                prefix = "CC "
            item = QTreeWidgetItem([f"{prefix}{path.name}"])
            item.setToolTip(0, str(path))
            item.setData(0, Qt.UserRole, str(path))
            parent_item.addChild(item)
            if selected and path == selected.resolve():
                selected_item = item
        root_item.setExpanded(True)
        if selected_item:
            parent = selected_item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            self.episode_list.setCurrentItem(selected_item)
        self.folder_label.setText(f"{folder.name}  ·  {len(library_files)} 个可用文件")
        self.folder_label.setToolTip(str(folder))
        self.settings.setValue("last_folder", str(folder))
        return library_files

    def toggle_episode_sidebar(self) -> None:
        visible = not self.episode_sidebar.isVisible()
        self.episode_sidebar.setVisible(visible)
        self.sidebar_toggle.setText(">" if visible else "<")
        self.sidebar_toggle.setToolTip("收起文件夹树" if visible else "展开文件夹树")

    def episode_selected(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path_text = item.data(0, Qt.UserRole)
        if not path_text:
            return
        path = Path(path_text)
        if not path.exists():
            return
        if path.suffix.lower() in MEDIA_EXTENSIONS:
            if path != self.media_path:
                self.load_media(path, keep_library=True)
        elif path.suffix.lower() in SUBTITLE_EXTENSIONS:
            self.load_subtitle(path)

    def _skip_settings_key(self) -> str:
        if not self.media_path:
            return ""
        digest = hashlib.sha1(str(self.media_path.resolve()).casefold().encode("utf-8")).hexdigest()
        return f"skipped/{digest}"

    def _load_skipped_texts(self) -> None:
        raw = self.settings.value(self._skip_settings_key(), "[]", str) if self.media_path else "[]"
        try:
            self.skipped_texts = set(json.loads(raw))
        except (TypeError, ValueError):
            self.skipped_texts = set()

    def _save_skipped_texts(self) -> None:
        key = self._skip_settings_key()
        if key:
            self.settings.setValue(key, json.dumps(sorted(self.skipped_texts), ensure_ascii=False))

    def _highlight_settings_key(self) -> str:
        source = self.media_path or self.subtitle_path
        if not source:
            return ""
        digest = hashlib.sha1(str(source.resolve()).casefold().encode("utf-8")).hexdigest()
        return f"highlights/{digest}"

    def _load_highlight_notes(self) -> None:
        key = self._highlight_settings_key()
        raw = self.settings.value(key, "[]", str) if key else "[]"
        self.highlight_notes = []
        try:
            items = json.loads(raw)
            for item in items:
                self.highlight_notes.append(
                    HighlightNote(
                        index=int(item["index"]),
                        sentence=str(item["sentence"]),
                        selected=str(item["selected"]),
                        selection_start=int(item["selection_start"]),
                        selection_end=int(item["selection_end"]),
                        speaker=str(item.get("speaker", "")),
                        start_ms=int(item.get("start_ms", 0)),
                        created_at=datetime.fromisoformat(item["created_at"]),
                    )
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.highlight_notes = []

    def _save_highlight_notes(self) -> None:
        key = self._highlight_settings_key()
        if not key:
            return
        payload = [
            {
                "index": note.index,
                "sentence": note.sentence,
                "selected": note.selected,
                "selection_start": note.selection_start,
                "selection_end": note.selection_end,
                "speaker": note.speaker,
                "start_ms": note.start_ms,
                "created_at": note.created_at.isoformat(),
            }
            for note in self.highlight_notes
        ]
        self.settings.setValue(key, json.dumps(payload, ensure_ascii=False))

    def _annotation_settings_key(self) -> str:
        source = self.media_path or self.subtitle_path
        if not source:
            return ""
        digest = hashlib.sha1(str(source.resolve()).casefold().encode("utf-8")).hexdigest()
        return f"annotations/{digest}"

    def _load_annotation_notes(self) -> None:
        key = self._annotation_settings_key()
        raw = self.settings.value(key, "[]", str) if key else "[]"
        self.annotation_notes = []
        try:
            items = json.loads(raw)
            for item in items:
                self.annotation_notes.append(
                    AnnotationNote(
                        index=int(item["index"]),
                        sentence=str(item["sentence"]),
                        quote=str(item.get("quote", "")),
                        note=str(item["note"]),
                        selection_start=int(item.get("selection_start", 0)),
                        selection_end=int(item.get("selection_end", 0)),
                        speaker=str(item.get("speaker", "")),
                        start_ms=int(item.get("start_ms", 0)),
                        created_at=datetime.fromisoformat(item["created_at"]),
                    )
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.annotation_notes = []

    def _save_annotation_notes(self) -> None:
        key = self._annotation_settings_key()
        if not key:
            return
        payload = [
            {
                "index": note.index,
                "sentence": note.sentence,
                "quote": note.quote,
                "note": note.note,
                "selection_start": note.selection_start,
                "selection_end": note.selection_end,
                "speaker": note.speaker,
                "start_ms": note.start_ms,
                "created_at": note.created_at.isoformat(),
            }
            for note in self.annotation_notes
        ]
        self.settings.setValue(key, json.dumps(payload, ensure_ascii=False))

    def load_folder(self, folder: Path) -> None:
        library_files = self.populate_episode_sidebar(folder)
        if not library_files:
            QMessageBox.warning(self, APP_NAME, "这个文件夹及其子文件夹里没有找到支持的媒体或字幕。")
            return
        self.episode_sidebar.show()
        self.sidebar_toggle.setText(">")
        media_files = [path for path in library_files if path.suffix.lower() in MEDIA_EXTENSIONS]
        if media_files:
            self.load_media(media_files[0], keep_library=True)
        else:
            self.load_subtitle(library_files[0])
            self.statusBar().showMessage(f"已读取整个文件夹；找到 {len(library_files)} 个字幕 / 文本，未找到媒体文件")

    def load_media(self, path: Path, keep_library: bool = False) -> None:
        self.player.stop()
        self.preview_player.stop()
        self.thumbnail_popup.hide()
        self.thumbnail_image.clear()
        self.thumbnail_image.setText("拖动进度条预览")
        self.media_path = path
        self.attempts.clear()
        self._load_skipped_texts()
        self._load_highlight_notes()
        self._load_annotation_notes()
        self.captions.clear()
        self.synced_with_embedded = False
        self.current_index = 0
        if not keep_library or not self.library_root:
            self.populate_episode_sidebar(path.parent, path)
        is_audio = path.suffix.lower() in AUDIO_EXTENSIONS
        self.video.setVisible(not is_audio)
        self.audio_panel.setVisible(is_audio)
        if is_audio:
            self.audio_panel.setText(f"♫\n{path.name}\n\n音频听写模式")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if is_audio:
            self.preview_player.setSource(QUrl())
        else:
            self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        media_kind = "音频" if is_audio else "视频"
        self.statusBar().showMessage(f"已载入{media_kind}：{path.name}，正在查找字幕……")
        matching = find_matching_subtitle(path, self.library_root if keep_library else path.parent)
        if matching:
            self.load_subtitle(matching)
            return
        if not self.load_embedded_subtitle(silent=True):
            self.subtitle_path = None
            self.counter.setText("未找到字幕")
            self.score_label.setText("可点击“打开字幕”选择文件")
            self.statusBar().showMessage(f"已载入 {path.name}；未发现外部或内嵌文字字幕")

    def load_embedded_subtitle(self, silent: bool = False) -> bool:
        if not self.media_path:
            if not silent:
                QMessageBox.information(self, APP_NAME, "请先打开一个视频或音频。")
            return False
        if self.media_path.suffix.lower() in AUDIO_EXTENSIONS and self.media_path.suffix.lower() != ".mka":
            if not silent:
                QMessageBox.information(self, APP_NAME, "普通音频文件通常不包含可用字幕。\n请选择配套的 SRT、ASS、LRC 或 TXT。")
            return False
        self.statusBar().showMessage("正在提取媒体内嵌字幕；大文件可能需要几十秒……")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            embedded = extract_embedded_subtitle(self.media_path)
        finally:
            QApplication.restoreOverrideCursor()
        if embedded:
            self.load_subtitle(embedded, embedded=True)
            return True
        else:
            if not silent:
                QMessageBox.information(self, APP_NAME, "没有找到可转换的内嵌文字字幕。\n图片型 PGS 字幕无法用于拼写判定。")
            return False

    def load_subtitle(self, path: Path, embedded: bool = False) -> None:
        try:
            captions, plain = parse_subtitle(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"字幕读取失败：\n{exc}")
            return
        if not captions:
            QMessageBox.warning(self, APP_NAME, "字幕文件里没有找到可练习的句子。")
            return
        self.subtitle_path = path
        if not self.media_path:
            self._load_highlight_notes()
            self._load_annotation_notes()
        self.synced_with_embedded = False
        sync_note = ""
        if not embedded and self.media_path and not plain:
            can_have_embedded_subtitles = self.media_path.suffix.lower() in VIDEO_EXTENSIONS | {".mka"}
            synced = captions
            if can_have_embedded_subtitles:
                self.statusBar().showMessage("正在用媒体内嵌英文字幕重建精准分句，首次可能需要几十秒……")
                QApplication.processEvents()
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    synced, translated_count = synchronize_with_embedded(self.media_path, captions)
                finally:
                    QApplication.restoreOverrideCursor()
            if synced is captions:
                captions, merged_count = repair_external_only_captions(captions)
                self.synced_with_embedded = False
                sync_note = "（仅外部字幕模式"
                if merged_count:
                    sync_note += f"，已自动合并 {merged_count} 个明显碎片"
                sync_note += "）"
            else:
                captions = synced
                self.synced_with_embedded = True
                self.offset_spin.setValue(0.0)
                sync_note = f"（内嵌英文精准时间轴，{translated_count} 句已匹配中文）"
        self.captions = captions
        self.plain_text_timing = plain
        self.current_index = 0
        if plain and self.player.duration() > 0:
            self.captions = retime_plain_text(self.captions, self.player.duration())
        self._refresh_proper_nouns()
        self.refresh_sentence_combo()
        self.load_current_caption()
        source = "媒体内嵌字幕" if embedded else path.name
        suffix = "（TXT 已按媒体时长自动分段）" if plain else ""
        self.statusBar().showMessage(f"已载入 {len(captions)} 句：{source}{suffix}{sync_note}")

    def refresh_sentence_combo(self) -> None:
        self.sentence_combo.blockSignals(True)
        self.sentence_combo.clear()
        for index, _caption in enumerate(self.captions):
            self.sentence_combo.addItem(f"第 {index + 1} 句")
        self.sentence_combo.blockSignals(False)

    def _refresh_proper_nouns(self) -> None:
        proper: set[str] = set()
        pronouns = {"i", "im", "ive", "ill", "id"}
        for caption in self.captions:
            proper.update(token for token in tokenize(caption.speaker) if token)
            cursor = 0
            for token in tokenize(caption.text):
                start = caption.text.find(token, cursor)
                prefix = caption.text[:max(0, start)].rstrip()
                sentence_initial = not prefix or prefix[-1] in ".!?…"
                cursor = max(cursor, start + len(token))
                simplified = token.casefold().replace("’", "").replace("'", "")
                if not sentence_initial and len(token) > 1 and token[0].isupper() and simplified not in pronouns | {"ok", "okay"}:
                    proper.add(token)
        self.proper_nouns = proper

    def jump_to_caption(self, index: int) -> None:
        if 0 <= index < len(self.captions):
            self.player.pause()
            self.current_index = index
            self.load_current_caption()

    def duration_changed(self, duration: int) -> None:
        self.seek.setRange(0, duration)
        if self.plain_text_timing and self.captions:
            self.captions = retime_plain_text(self.captions, duration)
            self.load_current_caption()

    def seek_pressed(self) -> None:
        self.slider_dragging = True
        self.seek_preview_moved(self.seek.value())

    def seek_preview_moved(self, position: int) -> None:
        if not self.media_path or self.media_path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        self.preview_position_ms = max(0, int(position))
        self.thumbnail_time.setText(format_ms(self.preview_position_ms))
        current_pixmap = self.thumbnail_image.pixmap()
        if current_pixmap is None or current_pixmap.isNull():
            self.thumbnail_image.setText("正在读取缩略图…")
        self._position_thumbnail_popup(self.preview_position_ms)
        self.thumbnail_popup.show()
        self.preview_timer.start()

    def _position_thumbnail_popup(self, position: int) -> None:
        duration = max(1, self.player.duration())
        ratio = min(1.0, max(0.0, position / duration))
        slider_x = round(10 + ratio * max(1, self.seek.width() - 20))
        popup_size = self.thumbnail_popup.sizeHint()
        anchor = self.seek.mapToGlobal(QPoint(slider_x, 0))
        x = anchor.x() - popup_size.width() // 2
        y = anchor.y() - popup_size.height() - 10
        screen = QApplication.screenAt(anchor)
        if screen:
            available = screen.availableGeometry()
            x = min(max(x, available.left() + 4), available.right() - popup_size.width() - 4)
            if y < available.top():
                y = self.seek.mapToGlobal(QPoint(slider_x, self.seek.height() + 10)).y()
        self.thumbnail_popup.move(x, y)

    def request_thumbnail_frame(self) -> None:
        if not self.slider_dragging or not self.media_path or self.media_path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        self.preview_player.setPosition(self.preview_position_ms)
        self.preview_player.play()

    def thumbnail_frame_changed(self, frame) -> None:
        if not self.slider_dragging or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(
            self.thumbnail_image.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.thumbnail_image.setText("")
        self.thumbnail_image.setPixmap(pixmap)
        self.preview_player.pause()

    def subtitle_offset_changed(self, seconds: float) -> None:
        self.subtitle_offset_ms = int(seconds * 1000)
        self.settings.setValue("subtitle_offset_seconds", seconds)
        if self.captions:
            self.segment_playing = False
            self.player.pause()
            self.player.setPosition(max(0, self.captions[self.current_index].start_ms + self.subtitle_offset_ms))
        self.statusBar().showMessage(f"字幕时间已校准 {seconds:+.1f} 秒")

    def lead_padding_changed(self, seconds: float) -> None:
        self.lead_padding_ms = int(seconds * 1000)
        self.settings.setValue("lead_padding_seconds", seconds)

    def tail_padding_changed(self, seconds: float) -> None:
        self.tail_padding_ms = int(seconds * 1000)
        self.settings.setValue("tail_padding_seconds", seconds)

    def hint_mode_changed(self, _index: int = -1) -> None:
        mode = self.hint_combo.currentData()
        self.settings.setValue("hint_mode", mode)
        self.word_track.set_hint_mode(mode)

    def auto_align_subtitles(self) -> None:
        if not self.media_path or not self.captions:
            QMessageBox.information(self, APP_NAME, "请先打开媒体和字幕。")
            return
        if self.media_path.suffix.lower() in AUDIO_EXTENSIONS and self.media_path.suffix.lower() != ".mka":
            QMessageBox.information(self, APP_NAME, "普通音频没有内嵌对话字幕可用于自动对齐。\n请使用“字幕校准”手动微调。")
            return
        self.statusBar().showMessage("正在比较外部字幕和媒体内嵌字幕……")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            offset = detect_subtitle_offset(self.media_path, self.captions)
        finally:
            QApplication.restoreOverrideCursor()
        if offset is None:
            QMessageBox.information(self, APP_NAME, "无法找到足够的相同台词自动对齐。\n可以用“字幕校准”手动微调。")
            return
        seconds = offset / 1000.0
        self.offset_spin.setValue(seconds)
        self.statusBar().showMessage(f"自动对齐完成：字幕 {seconds:+.2f} 秒")

    def _segment_bounds(self) -> tuple[int, int]:
        caption = self.captions[self.current_index]
        start = max(0, caption.start_ms + self.subtitle_offset_ms - self.lead_padding_ms)
        end = caption.end_ms + self.subtitle_offset_ms + self.tail_padding_ms
        if self.player.duration() > 0:
            end = min(end, self.player.duration())
        return start, max(start + 200, end)

    def position_changed(self, position: int) -> None:
        if not self.slider_dragging:
            self.seek.setValue(position)
        self.time_label.setText(f"{format_ms(position)} / {format_ms(self.player.duration())}")
        if self.subtitle_toggle.isChecked() and not self.segment_playing:
            self._update_continuous_subtitle(position)
        if self.segment_playing and self.captions:
            start, end = self._segment_bounds()
            if position >= max(start + 100, end - 35):
                self.segment_playing = False
                self.player.pause()
                self.player.setPosition(end)
                if self.loop_checkbox.isChecked():
                    self.loop_request_id += 1
                    request_id = self.loop_request_id
                    caption_index = self.current_index
                    QTimer.singleShot(180, lambda: self.repeat_if_current(request_id, caption_index))

    def repeat_if_current(self, request_id: int, caption_index: int) -> None:
        if (
            request_id == self.loop_request_id
            and caption_index == self.current_index
            and self.loop_checkbox.isChecked()
        ):
            self.play_current_segment()

    def seek_released(self) -> None:
        self.slider_dragging = False
        self.preview_timer.stop()
        self.preview_player.pause()
        self.thumbnail_popup.hide()
        self.loop_request_id += 1
        self.segment_playing = False
        self.player.setPosition(self.seek.value())

    def loop_setting_changed(self, checked: bool) -> None:
        if not checked:
            self.loop_request_id += 1

    def playback_state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_button.setText("暂停本句" if playing and self.segment_playing else "播放本句")
        self.normal_play_button.setText("暂停连续播放" if playing and not self.segment_playing else "连续播放")

    def media_error(self, error, error_text: str) -> None:
        if error_text:
            self.statusBar().showMessage(f"媒体播放错误：{error_text}")

    def play_current_segment(self) -> None:
        self.loop_request_id += 1
        if not self.media_path:
            self.choose_media()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState and self.segment_playing:
            self.player.pause()
            self.segment_playing = False
            return
        if not self.captions:
            self.player.play()
            return
        start, _ = self._segment_bounds()
        self.player.setPosition(start)
        self.segment_playing = True
        self.player.play()
        self.input.setFocus()

    def play_normal(self) -> None:
        self.loop_request_id += 1
        if not self.media_path:
            self.choose_media()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState and not self.segment_playing:
            self.player.pause()
            return
        self.segment_playing = False
        self.player.play()

    def load_current_caption(self) -> None:
        if not self.captions:
            return
        self.loop_request_id += 1
        caption = self.captions[self.current_index]
        self.sentence_combo.blockSignals(True)
        self.sentence_combo.setCurrentIndex(self.current_index)
        self.sentence_combo.blockSignals(False)
        words = tokenize(caption.text)
        skipped = caption.text in self.skipped_texts
        skipped_text = "　·　已跳过" if skipped else ""
        self.counter.setText(f"第 {self.current_index + 1} / {len(self.captions)} 句　·　{len(words)} 个词{skipped_text}")
        self.speaker_label.setText(f"说话人：{caption.speaker}" if caption.speaker else "说话人：字幕未标注（未合并相邻台词）")
        duration_seconds = max(0.1, (caption.end_ms - caption.start_ms) / 1000.0)
        words_per_second = len(words) / duration_seconds
        self.timing_warning.setText("⚠ 这句时间轴可能偏短，建议自动对齐或跳过" if words_per_second > 4.2 else "")
        total_attempts = len(self.attempts)
        if total_attempts:
            correct = sum(a.correct_words for a in self.attempts)
            total = sum(a.total_words for a in self.attempts)
            self.score_label.setText(f"累计正确率 {correct / max(1, total) * 100:.1f}%")
        else:
            self.score_label.setText("听一句，写一句")
        self.word_track.set_sentence(caption.text)
        self.input.clear()
        self.answer_label.clear()
        self.original_text.clear()
        self.original_panel.hide()
        self.translation_label.clear()
        self.translation_label.hide()
        self.answer_button.setText("显示答案")
        self.skip_button.setText("恢复此句" if skipped else "无对白 / 不重要，跳过")
        self.player.setPosition(max(0, caption.start_ms + self.subtitle_offset_ms))
        self.segment_playing = False
        self.update_subtitle_overlay()
        self.input.setFocus()

    def _current_highlights(self) -> list[HighlightNote]:
        if not self.captions:
            return []
        sentence = self.captions[self.current_index].text
        return [
            note
            for note in self.highlight_notes
            if note.index == self.current_index and note.sentence == sentence
        ]

    def _current_annotations(self) -> list[AnnotationNote]:
        if not self.captions:
            return []
        sentence = self.captions[self.current_index].text
        return [
            note
            for note in self.annotation_notes
            if note.index == self.current_index and note.sentence == sentence
        ]

    def _show_original_sentence(self) -> None:
        if not self.captions:
            return
        sentence = self.captions[self.current_index].text
        if self.original_text.toPlainText() != sentence:
            self.original_text.setPlainText(sentence)
        self._apply_highlights()
        self.original_panel.show()

    def _apply_highlights(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        text_length = len(self.original_text.toPlainText())
        for note in self._current_highlights():
            start = min(max(0, note.selection_start), text_length)
            end = min(max(start, note.selection_end), text_length)
            if end <= start:
                continue
            cursor = QTextCursor(self.original_text.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = QTextCharFormat()
            selection.format.setBackground(QColor("#ffe066"))
            selection.format.setForeground(QColor("#111318"))
            selections.append(selection)
        for note in self._current_annotations():
            start = min(max(0, note.selection_start), text_length)
            end = min(max(start, note.selection_end), text_length)
            if end <= start:
                continue
            cursor = QTextCursor(self.original_text.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = QTextCharFormat()
            selection.format.setBackground(QColor("#274b78"))
            selection.format.setForeground(QColor("#dbeafe"))
            selections.append(selection)
        self.original_text.setExtraSelections(selections)

    def _selected_original_span(self) -> tuple[int, int, str]:
        cursor = self.original_text.textCursor()
        start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        sentence = self.original_text.toPlainText()
        while start < end and sentence[start].isspace():
            start += 1
        while end > start and sentence[end - 1].isspace():
            end -= 1
        return start, end, sentence[start:end]

    def show_original_context_menu(self, position: QPoint) -> None:
        if not self.captions or not self.original_panel.isVisible():
            return
        start, end, selected = self._selected_original_span()
        menu = QMenu(self.original_text)
        highlight_action = menu.addAction("🖍 荧光笔")
        highlight_action.setEnabled(bool(selected))
        highlight_action.triggered.connect(self.highlight_selected_text)
        annotation_action = menu.addAction("📝 添加文字笔记…")
        annotation_action.triggered.connect(self.add_annotation_note)
        clear_action = menu.addAction("⌫ 清除所选荧光格式")
        clear_action.setEnabled(bool(selected))
        clear_action.triggered.connect(self.clear_selected_highlight)
        current_annotations = [
            note for note in self._current_annotations()
            if selected and note.selection_start < end and note.selection_end > start
        ]
        delete_note_action = menu.addAction("🗑 删除所选文字的笔记")
        delete_note_action.setEnabled(bool(current_annotations))
        delete_note_action.triggered.connect(lambda: self.delete_annotation_notes(current_annotations))
        menu.addSeparator()
        copy_action = menu.addAction("📋 复制所选文字")
        copy_action.setEnabled(bool(selected))
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(selected))
        menu.exec(self.original_text.mapToGlobal(position))

    def highlight_selected_text(self) -> None:
        if not self.captions or not self.original_panel.isVisible():
            QMessageBox.information(self, APP_NAME, "请先显示答案，再在原句里拖动选择要收藏的词。")
            return
        start, end, selected = self._selected_original_span()
        sentence = self.original_text.toPlainText()
        if not selected:
            QMessageBox.information(self, APP_NAME, "请先用鼠标拖动选择一个词或短语。")
            return
        duplicates = [
            note for note in self.highlight_notes
            if (
            note.index == self.current_index
            and note.sentence == sentence
            and note.selection_start == start
            and note.selection_end == end
            )
        ]
        if duplicates:
            duplicate_ids = {id(note) for note in duplicates}
            self.highlight_notes = [note for note in self.highlight_notes if id(note) not in duplicate_ids]
            message = f"已取消荧光标记：{selected}"
        else:
            caption = self.captions[self.current_index]
            self.highlight_notes.append(
                HighlightNote(
                    index=self.current_index,
                    sentence=sentence,
                    selected=selected,
                    selection_start=start,
                    selection_end=end,
                    speaker=caption.speaker,
                    start_ms=caption.start_ms,
                )
            )
            message = f"已用荧光笔标记：{selected}"
        self._save_highlight_notes()
        self._apply_highlights()
        self.statusBar().showMessage(f"{message}（共 {len(self.highlight_notes)} 处）")

    def add_annotation_note(self) -> None:
        if not self.captions or not self.original_panel.isVisible():
            return
        start, end, selected = self._selected_original_span()
        prompt_quote = selected or "（整句）"
        note_text, accepted = QInputDialog.getMultiLineText(
            self,
            "添加文字笔记",
            f"批注原文：{prompt_quote}\n\n请输入笔记：",
            "",
        )
        note_text = note_text.strip()
        if not accepted or not note_text:
            return
        caption = self.captions[self.current_index]
        self.annotation_notes.append(
            AnnotationNote(
                index=self.current_index,
                sentence=caption.text,
                quote=selected,
                note=note_text,
                selection_start=start,
                selection_end=end,
                speaker=caption.speaker,
                start_ms=caption.start_ms,
            )
        )
        self._save_annotation_notes()
        self._apply_highlights()
        self.statusBar().showMessage(f"已添加文字笔记（共 {len(self.annotation_notes)} 条）")

    def clear_selected_highlight(self) -> None:
        start, end, selected = self._selected_original_span()
        if not selected:
            return
        before = len(self.highlight_notes)
        self.highlight_notes = [
            note for note in self.highlight_notes
            if not (
                note.index == self.current_index
                and note.sentence == self.original_text.toPlainText()
                and note.selection_start < end
                and note.selection_end > start
            )
        ]
        if len(self.highlight_notes) != before:
            self._save_highlight_notes()
            self._apply_highlights()
            self.statusBar().showMessage("已清除所选文字的荧光格式")

    def delete_annotation_notes(self, notes: list[AnnotationNote]) -> None:
        note_ids = {id(note) for note in notes}
        self.annotation_notes = [note for note in self.annotation_notes if id(note) not in note_ids]
        self._save_annotation_notes()
        self._apply_highlights()
        self.statusBar().showMessage(f"已删除 {len(notes)} 条文字笔记")

    def clear_current_highlights(self) -> None:
        current = self._current_highlights()
        if not current:
            self.statusBar().showMessage("这句还没有荧光标记")
            return
        current_ids = {id(note) for note in current}
        self.highlight_notes = [note for note in self.highlight_notes if id(note) not in current_ids]
        self._save_highlight_notes()
        self._apply_highlights()
        self.statusBar().showMessage("已清除本句的荧光标记")

    def update_subtitle_overlay(self) -> None:
        if self.subtitle_toggle.isChecked() and self.captions:
            self.subtitle_line.setText(self.captions[self.current_index].text)
            self.subtitle_line.show()
        else:
            self.subtitle_line.hide()

    def _update_continuous_subtitle(self, position: int) -> None:
        subtitle_time = position - self.subtitle_offset_ms
        text = ""
        for caption in self.captions:
            if caption.start_ms <= subtitle_time <= caption.end_ms:
                text = caption.text
                break
            if caption.start_ms > subtitle_time:
                break
        self.subtitle_line.setText(text)

    def submit_answer(self) -> None:
        if not self.captions:
            return
        typed = self.input.text().strip()
        expected = self.captions[self.current_index].text
        if expected in self.skipped_texts:
            self.skipped_texts.remove(expected)
            self._save_skipped_texts()
        correct, results = compare_words(expected, typed, self.proper_nouns)
        total = len(tokenize(expected))
        attempt = Attempt(self.current_index, expected, typed, correct, total, results)
        self.attempts = [a for a in self.attempts if a.index != self.current_index]
        self.attempts.append(attempt)
        self.attempts.sort(key=lambda a: a.index)
        self.word_track.reveal(True)
        self.answer_button.setText("收起答案，再来一遍")
        wrong = []
        for expected_word, typed_word, ok in results:
            if ok:
                continue
            item = f"{typed_word or '漏'}→{expected_word or '多余'}"
            reason = explain_spelling_difference(expected_word, typed_word) if expected_word else ""
            if reason:
                item += f"（{reason}）"
            wrong.append(item)
        detail = "全部正确！" if not wrong else "需要注意：" + "；".join(wrong)
        self._show_original_sentence()
        self.answer_label.setText(f"本句 {correct}/{total}（{attempt.score:.1f}%）　{detail}")
        self._show_translation()
        all_correct = sum(a.correct_words for a in self.attempts)
        all_words = sum(a.total_words for a in self.attempts)
        self.score_label.setText(f"累计正确率 {all_correct / max(1, all_words) * 100:.1f}%")

    def handle_enter(self) -> None:
        if not self.captions:
            return
        if self.word_track.revealed:
            self.move_caption(1, play=True)
        else:
            self.submit_answer()

    def toggle_answer(self) -> None:
        if not self.captions:
            return
        if self.word_track.revealed:
            self.word_track.reveal(False)
            self.input.clear()
            self.answer_label.clear()
            self.original_text.clear()
            self.original_panel.hide()
            self.translation_label.clear()
            self.translation_label.hide()
            self.answer_button.setText("显示答案")
            self.input.setFocus()
        else:
            self.word_track.reveal(True)
            self._show_original_sentence()
            self.answer_label.setText("可在上方原句中拖动选择单词或短语，右键使用荧光笔或添加文字笔记。")
            self._show_translation()
            self.answer_button.setText("收起答案，再来一遍")

    def _show_translation(self) -> None:
        translation = self.captions[self.current_index].translation.strip()
        if translation:
            self.translation_label.setText(f"中文：{translation}")
            self.translation_label.show()
        else:
            self.translation_label.clear()
            self.translation_label.hide()

    def merge_with_next(self) -> None:
        if not self.captions or self.current_index >= len(self.captions) - 1:
            self.statusBar().showMessage("已经是最后一句，没有可合并的下一句")
            return
        current = self.captions[self.current_index]
        following = self.captions[self.current_index + 1]
        if current.speaker and following.speaker and current.speaker != following.speaker:
            QMessageBox.information(
                self,
                APP_NAME,
                f"不能合并：这是两个人的台词。\n{current.speaker} → {following.speaker}",
            )
            return
        translation = " ".join(value for value in (current.translation, following.translation) if value)
        merged = Caption(
            current.start_ms,
            following.end_ms,
            f"{current.text.rstrip()} {following.text.lstrip()}",
            current.speaker or following.speaker,
            translation,
        )
        self.captions[self.current_index:self.current_index + 2] = [merged]
        self.attempts = [attempt for attempt in self.attempts if attempt.index < self.current_index]
        self.refresh_sentence_combo()
        self.load_current_caption()
        self.statusBar().showMessage("已将当前句与下一句合并，会播放到合并后的结尾")

    def toggle_skip_current(self) -> None:
        if not self.captions:
            return
        text = self.captions[self.current_index].text
        if text in self.skipped_texts:
            self.skipped_texts.remove(text)
            self._save_skipped_texts()
            self.load_current_caption()
            self.statusBar().showMessage("已恢复这句的练习")
            return
        self.skipped_texts.add(text)
        self.attempts = [attempt for attempt in self.attempts if attempt.index != self.current_index]
        self._save_skipped_texts()
        self.statusBar().showMessage("已标记为无对白 / 不重要，不计入正确率")
        if self.current_index < len(self.captions) - 1:
            self.move_caption(1, play=True)
        else:
            self.load_current_caption()

    def move_caption(self, delta: int, play: bool = False) -> None:
        if not self.captions:
            return
        new_index = min(len(self.captions) - 1, max(0, self.current_index + delta))
        if new_index == self.current_index and delta > 0:
            self.statusBar().showMessage("已经是最后一句。可以导出本次练习报告。")
            return
        self.player.pause()
        self.current_index = new_index
        self.load_current_caption()
        if play:
            QTimer.singleShot(120, self.play_current_segment)

    def export_report(self, kind: str) -> None:
        if not self.attempts and not self.highlight_notes and not self.annotation_notes:
            QMessageBox.information(self, APP_NAME, "还没有练习记录或荧光笔记。先提交一句，或在原句中标记单词。")
            return
        base = self.media_path.stem if self.media_path else "shadowing练习"
        if kind == "txt":
            path, _ = QFileDialog.getSaveFileName(self, "导出 TXT", f"{base}_练习报告.txt", "文本文件 (*.txt)")
        else:
            path, _ = QFileDialog.getSaveFileName(self, "导出 Word", f"{base}_练习报告.docx", "Word 文档 (*.docx)")
        if not path:
            return
        try:
            if kind == "txt":
                export_txt(path, self.media_path.name if self.media_path else base, self.attempts, self.highlight_notes, self.annotation_notes)
            else:
                export_docx(path, self.media_path.name if self.media_path else base, self.attempts, self.highlight_notes, self.annotation_notes)
            self.statusBar().showMessage(f"练习报告已导出：{path}")
            QMessageBox.information(self, APP_NAME, f"导出完成：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"导出失败：\n{exc}")


def main() -> int:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShadowingTrainer.v8")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = resource_path("assets/shadowing_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.showMaximized()
    if len(sys.argv) > 1:
        startup_path = Path(sys.argv[1])
        if startup_path.exists():
            QTimer.singleShot(100, lambda: window.handle_dropped_path(str(startup_path)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
