"""メインウィンドウ：動画 + ニコ動コメントを重ねて再生する GUI。"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import (
    QAudioDevice,
    QAudioOutput,
    QMediaDevices,
    QMediaPlayer,
    QVideoFrame,
    QVideoSink,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .comment_parser import CommentParseError, CommentSet, load_comment_json
from .overlay import CommentOverlay
from .video_view import VideoView

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".flv")
FILTERS = {
    "video": "動画 (*.mp4 *.webm *.mkv *.mov *.m4v *.avi *.flv);;すべてのファイル (*)",
    "json": "コメントJSON (*.json);;すべてのファイル (*)",
}
SPEED_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
# QAudioDevice の説明文に付く「接続されていない」ことを示す表示
_DISCONNECTED_MARKERS = ("接続されませんでした", "not connected", "Not available")


def _is_connected(device: QAudioDevice) -> bool:
    """出力デバイスが実際に接続されているか（Windowsの「接続されませんでした」を除外）。"""
    if device.isNull():
        return False
    desc = device.description()
    return not any(marker in desc for marker in _DISCONNECTED_MARKERS)


def _connected_outputs() -> list[QAudioDevice]:
    """音声の出力先として選べるデバイス一覧。

    未接続の Bluetooth デバイスは内部名が「接続されませんでした」になり、
    接続済みは「機種名」になる。接続済みを前に、未接続（無効）を後ろに並べ、
    既定デバイスが未接続になっている場合の誤選択を防ぐ。
    """
    connected: list[QAudioDevice] = []
    inactive: list[QAudioDevice] = []
    for device in QMediaDevices.audioOutputs():
        if device.isNull():
            continue
        if _is_connected(device):
            connected.append(device)
        else:
            inactive.append(device)
    return connected + inactive


def _only_inactive_devices() -> bool:
    outputs = [d for d in QMediaDevices.audioOutputs() if not d.isNull()]
    return bool(outputs) and not any(_is_connected(d) for d in outputs)


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ニコ動コメントプレイヤー")
        self.resize(1280, 800)

        self.comment_set: Optional[CommentSet] = None
        self.video_path = ""
        self.json_path = ""
        self._slider_dragging = False
        self._speed_index = SPEED_STEPS.index(1.0)

        # --- マルチメディア ---------------------------------------------------
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_video_frame)

        # --- ビュー -----------------------------------------------------------
        self.video = VideoView(self)
        self.overlay = CommentOverlay(self.video)
        self.video.installEventFilter(self)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.video, 1)
        self._controls = self._build_controls()
        layout.addWidget(self._controls)
        self.setCentralWidget(container)
        self.statusBar()

        # デバイスはコントロール構築後に選ぶ（コンボボックスに反映するため）
        self._setup_audio_output()
        # ヘッドホン等の抜き差しを監視する（インスタンスのシグナルにつなぐ）
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)

        self.video.video_rect_changed.connect(self._on_video_rect_changed)
        self.video.clicked.connect(self.toggle_play)
        self.video.double_clicked.connect(self._toggle_fullscreen)

        # --- プレイヤー信号 ---------------------------------------------------
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        # --- 再生中の再描画 ---------------------------------------------------
        self.draw_timer = QTimer(self)
        self.draw_timer.setInterval(16)  # 約60fps
        self.draw_timer.timeout.connect(self._tick)
        self.draw_timer.start()

        self._install_shortcuts()
        self._update_status_text()
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------ UI構築
    def _build_controls(self) -> QWidget:
        bar = QWidget(self)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        self.seek = QSlider(Qt.Orientation.Horizontal, bar)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(self._on_seek_pressed)
        self.seek.sliderReleased.connect(self._on_seek_released)
        self.seek.sliderMoved.connect(self._on_seek_moved)
        layout.addWidget(self.seek)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.btn_play = QPushButton(bar)
        self.btn_play.setToolTip("再生 / 一時停止 (Space)")
        self.btn_play.clicked.connect(self.toggle_play)
        row.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00 / 00:00", bar)
        self.lbl_time.setMinimumWidth(110)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.lbl_time)

        btn = QPushButton("動画を開く", bar)
        btn.setToolTip("動画ファイルを開く (Ctrl+O)")
        btn.clicked.connect(self.open_video_dialog)
        row.addWidget(btn)

        btn = QPushButton("JSONを開く", bar)
        btn.setToolTip("ニコ動のコメントJSONを開く (Ctrl+J)")
        btn.clicked.connect(self.open_json_dialog)
        row.addWidget(btn)

        self.chk_comments = QCheckBox("コメント表示", bar)
        self.chk_comments.setChecked(True)
        self.chk_comments.toggled.connect(self.overlay.set_comments_enabled)
        row.addWidget(self.chk_comments)

        row.addSpacing(8)
        row.addWidget(QLabel("音量", bar))
        self.volume = QSlider(Qt.Orientation.Horizontal, bar)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(100)
        self.volume.setToolTip("音量 (↑ / ↓)")
        self.volume.valueChanged.connect(self._on_volume_changed)
        row.addWidget(self.volume)

        # ミュート
        self.btn_mute = QPushButton(bar)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("消音 (M)")
        self.btn_mute.setFixedWidth(32)
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        row.addWidget(self.btn_mute)

        # 出力デバイス（スピーカー / ヘッドホン / Bluetooth など）
        self.cmb_device = QComboBox(bar)
        self.cmb_device.setToolTip("音声の出力先")
        self.cmb_device.setMinimumWidth(170)
        self.cmb_device.setMaxVisibleItems(12)
        self.cmb_device.activated.connect(self._on_device_selected)
        row.addWidget(self.cmb_device)

        # デバイスの再読み込み（Bluetoothの接続後に押すと一覧が更新される）
        btn_audio_refresh = QPushButton("⟳", bar)
        btn_audio_refresh.setToolTip("音声デバイスを再読み込み")
        btn_audio_refresh.setFixedWidth(30)
        btn_audio_refresh.clicked.connect(self.refresh_audio_devices)
        row.addWidget(btn_audio_refresh)

        row.addWidget(QLabel("速度", bar))
        self.btn_speed = QPushButton("1.0x", bar)
        self.btn_speed.setToolTip("再生速度 (クリックで切替)")
        self.btn_speed.setFixedWidth(64)
        self.btn_speed.clicked.connect(self._cycle_speed)
        row.addWidget(self.btn_speed)

        self.btn_full = QPushButton(bar)
        self.btn_full.setToolTip("全画面 (F11 / Escで戻す)")
        self.btn_full.clicked.connect(self._toggle_fullscreen)
        row.addWidget(self.btn_full)

        row.addStretch(1)
        self.lbl_status = QLabel("", bar)
        self.lbl_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.lbl_status, 3)
        layout.addLayout(row)

        self._refresh_icons()
        return bar

    def _refresh_icons(self) -> None:
        style = self.style()
        self.btn_play.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setText("")
        self.btn_full.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        self.btn_full.setText("")
        self.btn_play.setFixedWidth(40)
        self.btn_full.setFixedWidth(40)
        self._update_mute_icon()

    def _update_mute_icon(self) -> None:
        muted = self.audio.isMuted() or self.volume.value() == 0
        pixmap = (
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if muted
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.btn_mute.setIcon(self.style().standardIcon(pixmap))

    def _install_shortcuts(self) -> None:
        def sc(key: str, slot) -> None:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

        sc("Space", self.toggle_play)
        sc("Left", lambda: self.seek_by(-5000))
        sc("Right", lambda: self.seek_by(5000))
        sc("Shift+Left", lambda: self.seek_by(-1000))
        sc("Shift+Right", lambda: self.seek_by(1000))
        sc("Up", lambda: self._change_volume(+5))
        sc("Down", lambda: self._change_volume(-5))
        sc("M", self.btn_mute.toggle)
        sc("Ctrl+R", self.refresh_audio_devices)
        sc("F11", self._toggle_fullscreen)
        sc("Esc", self._exit_fullscreen)
        sc("Ctrl+O", self.open_video_dialog)
        sc("Ctrl+J", self.open_json_dialog)
        sc("Ctrl+M", self.chk_comments.toggle)
        sc("Ctrl+Shift+N", self._jump_to_next_comment)

    # ------------------------------------------------------------------ ファイル
    def open_video_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "動画ファイルを開く", self._start_dir(), FILTERS["video"])
        if path:
            self.open_video(path)

    def open_json_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "コメントJSONを開く", self._start_dir(), FILTERS["json"])
        if path:
            self.load_json(path)

    def _start_dir(self) -> str:
        base = self.video_path or self.json_path or os.getcwd()
        return os.path.dirname(base) if os.path.isfile(base) else base

    def open_video(self, path: str) -> None:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(self, "動画を開けません", f"ファイルが見つかりません:\n{path}")
            return
        self.video_path = path
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._update_status_text()
        self._auto_pair_json()

    def load_json(self, path: str) -> Optional[CommentSet]:
        path = os.path.abspath(path)
        try:
            comment_set = load_comment_json(path)
        except CommentParseError as exc:
            QMessageBox.warning(self, "コメントJSONを読み込めません", str(exc))
            return None
        self.json_path = path
        self.comment_set = comment_set
        self.overlay.set_comments(comment_set.comments)
        self.overlay.set_current_time(self.player.position())
        if comment_set.warnings:
            self.statusBar().showMessage(comment_set.warnings[0], 12000)
        self._update_status_text()
        return comment_set

    def open_pair(self, video_path: str, json_path: str) -> None:
        """動画とJSONをまとめて開く（起動引数・ドラッグ&ドロップ用）。"""
        self.json_path = ""  # 自動ペア読み込みで上書きされないようにする
        self.open_video(video_path)
        self.load_json(json_path)
        self.player.pause()

    def _auto_pair_json(self) -> None:
        """動画と同じ名前のJSONが同じフォルダにあれば自動で読み込む。"""
        if not self.video_path:
            return
        candidate = os.path.splitext(self.video_path)[0] + ".json"
        if not os.path.isfile(candidate):
            return
        if os.path.abspath(candidate) == os.path.abspath(self.json_path):
            return
        self.load_json(candidate)

    # ------------------------------------------------------------------ 操作
    @Slot()
    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.source().isEmpty():
                self.open_video_dialog()
                return
            self.player.play()

    def seek_by(self, delta_ms: int) -> None:
        duration = self.player.duration()
        target = max(0, self.player.position() + delta_ms)
        if duration > 0:
            target = min(target, max(0, duration - 250))
        self.player.setPosition(target)
        self.overlay.set_current_time(target)
        self._update_time_label(target, duration)

    def _change_volume(self, delta: int) -> None:
        self.volume.setValue(max(0, min(100, self.volume.value() + delta)))

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        self.audio.setVolume(value / 100.0)
        # 音量を上げたら消音は自動で解除する（一般的なプレイヤーと同じ挙動）
        if value > 0 and self.audio.isMuted():
            self.btn_mute.blockSignals(True)
            self.btn_mute.setChecked(False)
            self.btn_mute.blockSignals(False)
            self.audio.setMuted(False)
        self._update_mute_icon()

    @Slot(bool)
    def _on_mute_toggled(self, muted: bool) -> None:
        self.audio.setMuted(muted)
        self._update_mute_icon()

    # ------------------------------------------------------------------ 音声出力
    def _setup_audio_output(self) -> None:
        """音声の出力先を選ぶ。

        Windows では既定デバイスが「ヘッドホン (接続されませんでした)」のように
        未接続のものになっていることがあるため、実際に接続されているデバイスを優先する。
        …ただし「接続されませんでした」が、未接続に設定した実デバイス（Bluetooth等）の
        表示名である場合もある。そのときは、そのデバイスを既定として選んでおき、
        Bluetooth を接続し直したあとに「出力先」から選び直せるように案内する。
        """
        outputs = _connected_outputs()
        if not outputs:
            self._refresh_device_combo([])
            self.statusBar().showMessage("音声出力デバイスが見つかりません（映像のみ再生します）", 10000)
            return

        first = outputs[0]
        if _is_connected(first):
            self.audio.setDevice(first)
            self._refresh_device_combo(outputs)
            return

        # すべて「接続されませんでした」: 既定デバイスを見出しにする
        default = QMediaDevices.defaultAudioOutput()
        if not default.isNull() and not _is_connected(default):
            self.audio.setDevice(default)
            label = default.description().replace(" (接続されませんでした)", "")
            self.statusBar().showMessage(
                f"接続されている音声デバイスがありません（既定: {label}）。"
                "Bluetooth イヤホン等を接続してから、「出力先」で選んでください。",
                20000,
            )
        else:
            self.audio.setDevice(first)
        self._refresh_device_combo(outputs)

    def _refresh_device_combo(self, outputs: list[QAudioDevice]) -> None:
        self.cmb_device.blockSignals(True)
        self.cmb_device.clear()
        current = self.audio.device()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        for index, device in enumerate(outputs):
            self.cmb_device.addItem(icon, device.description(), device.id().data())
            if device.id() == current.id():
                self.cmb_device.setCurrentIndex(index)
        if not outputs:
            self.cmb_device.addItem("音声デバイスなし")
            self.cmb_device.setEnabled(False)
        self.cmb_device.blockSignals(False)

    def refresh_audio_devices(self) -> None:
        """デバイス一覧を取り直す。Bluetooth 接続後に押すと、最新の状態になる。"""
        outputs = _connected_outputs()
        self._refresh_device_combo(outputs)
        connected = [d for d in outputs if _is_connected(d)]
        current = self.audio.device()
        if connected and (current.isNull() or not _is_connected(current)):
            # 今の出力先が未接続なら、見つかった接続済みデバイスへ一度だけ切り替える
            self.audio.setDevice(connected[0])
            self.statusBar().showMessage(f"音声の出力先を {connected[0].description()} にしました", 5000)
        elif connected:
            self._refresh_device_combo(outputs)
            self.statusBar().showMessage(
                f"音声デバイス {len(connected)} 件（接続中）。出力先は上の一覧から選択できます。", 5000
            )
        else:
            self.statusBar().showMessage(
                "接続中の音声デバイスが見つかりません。Bluetooth イヤホン等を接続し、"
                "接続されるまで少し待ってから再度 ⟳ を押してください。",
                10000,
            )
        if not outputs:
            self.cmb_device.setEnabled(False)

    @Slot(int)
    def _on_device_selected(self, index: int) -> None:
        outputs = _connected_outputs()
        if not (0 <= index < len(outputs)):
            return
        device = outputs[index]
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        position = self.player.position()
        self.audio.setDevice(device)
        # 出力の付け替えで音が飛ぶことがあるので、位置を保って再生し直す
        if was_playing:
            self.player.setPosition(position)
        self.statusBar().showMessage(f"音声の出力先: {device.description()}", 5000)

    @Slot()
    def _on_audio_outputs_changed(self) -> None:
        """ヘッドホン・BTイヤホンの接続/切断などでデバイス一覧が変わったとき。"""
        outputs = _connected_outputs()
        self._refresh_device_combo(outputs)
        current = self.audio.device()
        # 選択中のデバイスが接続済みなら、勝手に切り替えない
        if not current.isNull() and _is_connected(current):
            return
        connected = [d for d in outputs if _is_connected(d)]
        if connected:
            self.audio.setDevice(connected[0])
            self.statusBar().showMessage(
                f"音声の出力先を {connected[0].description()} に切り替えました", 5000
            )
            if self.player.source().isEmpty() is False and self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                # 一時停止中に音の出口だけ切り替えた場合は、そのまま待機させる
                pass
        else:
            self.statusBar().showMessage("音声デバイスが接続されていないため映像のみ再生します", 8000)

    def _cycle_speed(self) -> None:
        self._speed_index = (self._speed_index + 1) % len(SPEED_STEPS)
        speed = SPEED_STEPS[self._speed_index]
        self.player.setPlaybackRate(speed)
        self.btn_speed.setText(f"{speed:g}x")

    def _jump_to_next_comment(self) -> None:
        if not self.comment_set:
            return
        now = self.player.position()
        nxt = next((c.vpos_ms for c in self.comment_set.comments if c.vpos_ms > now), None)
        if nxt is not None:
            self.seek_by(nxt - now)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        # 全画面中は操作バーも隠して動画だけを表示する
        self._apply_immersive(self.isFullScreen())

    def _apply_immersive(self, immersive: bool) -> None:
        self._controls.setVisible(not immersive)
        self.statusBar().setVisible(not immersive)

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self._apply_immersive(False)

    # ------------------------------------------------------------------ スロット
    @Slot(QVideoFrame)
    def _on_video_frame(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.video.set_frame(image)

    @Slot(QRectF)
    def _on_video_rect_changed(self, rect: QRectF) -> None:
        self.overlay.set_video_rect(rect)

    @Slot(int)
    def _on_position_changed(self, pos: int) -> None:
        self.overlay.set_current_time(pos)
        if not self._slider_dragging:
            self.seek.blockSignals(True)
            self.seek.setValue(pos)
            self.seek.blockSignals(False)
        self._update_time_label(pos, self.player.duration())

    @Slot(int)
    def _on_duration_changed(self, duration: int) -> None:
        self.seek.setRange(0, max(0, duration))
        self.overlay.set_current_time(self.player.position())
        self._update_time_label(self.player.position(), duration)

    @Slot(QMediaPlayer.PlaybackState)
    def _on_state_changed(self, state) -> None:
        pixmap = (
            QStyle.StandardPixmap.SP_MediaPause
            if state == QMediaPlayer.PlaybackState.PlayingState
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.btn_play.setIcon(self.style().standardIcon(pixmap))

    @Slot(QMediaPlayer.Error, str)
    def _on_player_error(self, error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        QMessageBox.warning(
            self,
            "動画を再生できません",
            f"{message or '不明なエラー'}\n\n"
            "コーデックに対応していない可能性があります。\n"
            "MP4(H.264/AAC) に変換すると再生できます。",
        )

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.statusBar().showMessage("この動画は再生できない形式です。", 10000)

    def _on_seek_pressed(self) -> None:
        self._slider_dragging = True

    def _on_seek_moved(self, value: int) -> None:
        self.overlay.set_current_time(value)
        self._update_time_label(value, self.player.duration())

    def _on_seek_released(self) -> None:
        self._slider_dragging = False
        self.player.setPosition(self.seek.value())
        self.overlay.set_current_time(self.seek.value())

    def _tick(self) -> None:
        if self.comment_set and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            # positionChanged の更新間隔を補い、コメントを滑らかに動かす
            self.overlay.set_current_time(self.player.position())
        if self.comment_set:
            self.overlay.update()

    # ------------------------------------------------------------------ 表示
    def _update_time_label(self, pos: int, duration: int) -> None:
        self.lbl_time.setText(f"{_fmt(pos)} / {_fmt(duration)}")

    def _update_status_text(self) -> None:
        parts: list[str] = []
        if self.video_path:
            parts.append(os.path.basename(self.video_path))
        if self.comment_set:
            parts.append(f"コメント {len(self.comment_set)} 件")
        if not parts:
            parts.append(
                "「動画を開く」「JSONを開く」でファイルを選択（同じ名前のJSONは自動で読み込みます）"
            )
        self.lbl_status.setText("  |  ".join(parts))

    def start_with_paths(self, paths: list[str]) -> None:
        video = next((p for p in paths if p.lower().endswith(VIDEO_EXTS)), None)
        json_file = next((p for p in paths if p.lower().endswith(".json")), None)
        if video and json_file:
            self.open_pair(video, json_file)
        elif video:
            self.open_video(video)
        elif json_file:
            self.load_json(json_file)

    # ------------------------------------------------------------------ イベント
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.video:
            if event.type() == QEvent.Type.Resize:
                self.overlay.setGeometry(0, 0, self.video.width(), self.video.height())
                self.overlay.raise_()
            elif event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                self.video.setFocus()
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.start_with_paths(paths)
            event.acceptProposedAction()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_icons()
        self.overlay.setGeometry(0, 0, self.video.width(), self.video.height())
        self.overlay.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)


def _fmt(ms: int) -> str:
    if not ms or ms <= 0:
        return "00:00"
    total = int(ms // 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
