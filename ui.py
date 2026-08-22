from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import requests

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QDialog, QProgressBar, QGraphicsDropShadowEffect,
)

_main_window_instance: 'MainWindow' | None = None

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 1000, 630
_MIN_W,     _MIN_H     = 960, 600
_LEFT_W  = 200
_RIGHT_W = 360
_BOTTOM_H = 56

_OS = platform.system()

class C:
    BG        = "#05070D"
    PANEL     = "#0A101C"
    PANEL2    = "#0D1524"
    BORDER    = "#1E2A3A"
    BORDER_B  = "#2A3A4F"
    BORDER_A  = "#1A2E3F"
    PRI       = "#00D4FF"
    PRI_DIM   = "#007A99"
    PRI_GHO   = "#001F2E"
    ACC       = "#D4AF37"
    ACC2      = "#FFB454"
    GREEN     = "#00E6A0"
    GREEN_D   = "#00AA66"
    RED       = "#FF4D6A"
    MUTED_C   = "#FF4D6A"
    TEXT      = "#E8F4FF"
    TEXT_DIM  = "#6E7F99"
    TEXT_MED  = "#9FB3CC"
    WHITE     = "#F5FAFF"
    DARK      = "#03060B"
    BAR_BG    = "#0D1524"
    GLASS     = "rgba(10, 16, 28, 0.72)"

def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

def _add_shadow(widget, radius=24, alpha=80, offset=(0, 8)):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(radius)
    effect.setColor(qcol("#000000", alpha))
    effect.setOffset(*offset)
    widget.setGraphicsEffect(effect)

class _SysMetrics:
    # Same as previous version, unchanged for reliability
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0
        self.gpu  = -1.0
        self.tmp  = -1.0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()
        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass
        return -1.0

    def _get_temp(self):
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass
        return -1.0

    def snapshot(self):
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }

_metrics = _SysMetrics()


class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._rings      = [0.0, 90.0, 180.0, 270.0]
        self._scan       = 0.0
        self._blink      = True
        self._blink_tick = 0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_halo = random.uniform(145, 190)
            elif self.muted:
                self._tgt_halo = random.uniform(15, 28)
            else:
                self._tgt_halo = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._halo += (self._tgt_halo - self._halo) * sp

        speeds = [1.3, -0.9, 2.0, -1.5] if self.speaking else [0.55, -0.35, 0.9, -0.65]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan = (self._scan + (3.0 if self.speaking else 1.3)) % 360

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        grad = QLinearGradient(0, 0, W, H)
        grad.setColorAt(0.0, qcol("#05070D"))
        grad.setColorAt(0.5, qcol("#0A101C"))
        grad.setColorAt(1.0, qcol("#05070D"))
        p.fillRect(self.rect(), QBrush(grad))

        glow_radius = fw * 0.45
        g = QRadialGradient(QPointF(cx, cy), glow_radius)
        g.setColorAt(0.0, qcol("#003344", 90))
        g.setColorAt(1.0, qcol("#05070D", 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - glow_radius, cy - glow_radius, glow_radius * 2, glow_radius * 2))

        ring_fractions = [0.48, 0.41, 0.34, 0.27]
        for i, frac in enumerate(ring_fractions):
            radius = fw * frac
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            base = self._rings[i]
            arc_len = 110
            gap = 55
            pen = QPen(qcol(C.PRI if i % 2 == 0 else C.PRI_DIM, min(255, int(self._halo))), 2 + i)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_len * 16))
                angle += arc_len + gap

        core_radius = fw * 0.31
        for i in range(10, 0, -1):
            r = core_radius * i / 10
            alpha = max(0, min(255, int(self._halo * 0.8 * (i / 10))))
            if self.muted:
                col = qcol(C.MUTED_C, alpha)
            else:
                col = qcol(C.PRI, alpha)
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        p.setBrush(QBrush(qcol("#0A101C", 220)))
        p.setPen(QPen(qcol(C.BORDER_B, 200), 2))
        p.drawEllipse(QRectF(cx - core_radius * 0.82, cy - core_radius * 0.82,
                             core_radius * 1.64, core_radius * 1.64))

        waveform_y = cy
        N = 37
        spacing = fw * 0.014
        x0 = cx - (N // 2) * spacing
        for i in range(N):
            x = x0 + i * spacing
            if self.muted:
                height = 2
                col = qcol(C.MUTED_C, 200)
            elif self.speaking:
                height = random.randint(6, int(fw * 0.09))
                col = qcol(C.PRI, 220) if height > 15 else qcol(C.PRI_DIM, 220)
            else:
                height = int(4 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                col = qcol(C.BORDER_B, 200)
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(x, waveform_y - height / 2),
                       QPointF(x, waveform_y + height / 2))

        status_y = cy + fw * 0.42
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        if self.muted:
            txt, col = "MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "SPEAKING", qcol(C.ACC)
        elif self.state == "THINKING":
            txt, col = "THINKING", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            txt, col = "PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            txt, col = "LISTENING", qcol(C.GREEN)
        else:
            txt, col = self.state, qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.drawText(QRectF(0, status_y, W, 30), Qt.AlignmentFlag.AlignCenter, txt)


class CircularGauge(QWidget):
    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self.setFixedSize(72, 72)
        self.setMinimumSize(72, 72)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        radius = min(W, H) * 0.40

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.BAR_BG, 200)))
        p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.BORDER_A), 1.5))
        p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        if self._value > 0:
            arc_color = qcol(C.RED if self._value > 85 else C.ACC if self._value > 65 else self._color)
            start_angle = 90 * 16
            span_angle = int(-self._value * 3.6 * 16)
            pen = QPen(arc_color, 5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                      start_angle, span_angle)

        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, cy - radius - 12, W, 14), Qt.AlignmentFlag.AlignCenter, self._label)

        p.setPen(QPen(qcol(self._color if self._text != "--" else C.TEXT_DIM), 1))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.drawText(QRectF(0, cy - 6, W, 18), Qt.AlignmentFlag.AlignCenter, self._text)


class MetricBar(QWidget):
    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self.setFixedHeight(40)
        self.setMinimumWidth(100)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2, 210)))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 6, 6)

        bar_h = 5
        bar_y = H - bar_h - 6
        bar_w = W - 14
        bar_x = 7
        fill_w = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        bar_col = qcol(C.RED if self._value > 85 else C.ACC if self._value > 65 else self._color)
        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(10, 4, 60, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 8, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)
    LONG_TEXT_THRESHOLD = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(10, 16, 28, 0.8);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                padding: 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text = self._queue.pop(0)
        self._pos = 0
        tl = self._text.lower()
        if tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"

        if len(self._text) > self.LONG_TEXT_THRESHOLD:
            self._insert_full_text()
            return
        self._tmr.start(6)

    def _insert_full_text(self):
        col = {
            "you": qcol(C.WHITE),
            "ai": qcol(C.PRI),
            "err": qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys": qcol(C.ACC2),
        }.get(self._tag, qcol(C.TEXT))
        cur = self.textCursor()
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(col))
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(self._text, fmt)
        cur.insertText("\n")
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        QTimer.singleShot(20, self._next)

    def _step(self):
        if self._pos < len(self._text):
            ch = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you": qcol(C.WHITE),
                "ai": qcol(C.PRI),
                "err": qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys": qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if size < 1024: return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else: return f"{size/1024**3:.1f} GB"

class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(90)
        self._current_file = None
        self._hovering = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self):
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    # Similar to before but modernised
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z = self._z
        W, H = self.width(), self.height()
        pad = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#0A101C" if z._drag_over else "#0D1524")
        p.setBrush(QBrush(bg_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 8, 8)

        border_col = qcol(C.GREEN if z._current_file else C.PRI if z._drag_over else C.BORDER)
        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 8, 8)

        if z._current_file:
            self._paint_file(p, W, H)
        elif z._drag_over:
            p.setFont(QFont("Segoe UI", 18))
            p.setPen(QPen(qcol(C.PRI), 1))
            p.drawText(QRectF(0, H/2-20, W, 32), Qt.AlignmentFlag.AlignCenter, "Release to load")
        else:
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(QRectF(0, H/2-10, W, 20), Qt.AlignmentFlag.AlignCenter, "Drop file here or click to browse")
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(QRectF(0, H/2+12, W, 16), Qt.AlignmentFlag.AlignCenter, "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QPen(qcol(C.TEXT), 1))
        p.drawText(QRectF(10, H/2-20, W-20, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{icon} {path.name}")
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(10, H/2+2, W-20, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{size_str} — click ✕ to remove")
        p.setPen(QPen(qcol(C.RED, 200), 1))
        p.drawText(QRectF(W-30, 0, 24, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    # Redesigned with modern glass, kept similar flow
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(5, 7, 13, 240);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        detected = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(10)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Segoe UI", font_size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("INITIALISATION REQUIRED", 14, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.TEXT_DIM))
        layout.addSpacing(6)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(6)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Segoe UI", 10))
        self._key_input.setFixedHeight(34)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{ background: #0A101C; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 10px; }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)

        layout.addWidget(_lbl("OPENROUTER API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._or_input = QLineEdit()
        self._or_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._or_input.setPlaceholderText("sk-or-…")
        self._or_input.setFont(QFont("Segoe UI", 10))
        self._or_input.setFixedHeight(34)
        self._or_input.setStyleSheet(f"""
            QLineEdit {{ background: #0A101C; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 10px; }}
            QLineEdit:focus {{ border: 1px solid {C.ACC2}; }}
        """)
        layout.addWidget(self._or_input)

        layout.addSpacing(6)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2, align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns = {}
        for key, label in [("windows","⊞ Windows"),("mac"," macOS"),("linux"," Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)

        layout.addSpacing(10)
        init_btn = QPushButton("INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        init_btn.setFixedHeight(38)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 6px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"QPushButton {{ background: {fg}; color: {bg}; border: none; border-radius: 4px; font-weight: bold; }}")
            else:
                btn.setStyleSheet(f"QPushButton {{ background: #0A101C; color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; border-radius: 4px; }}")

    def _submit(self):
        key = self._key_input.text().strip()
        or_key = self._or_input.text().strip()
        if not key or not or_key:
            return
        self.done.emit(key, or_key, self._sel_os)


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _camera_preview_signal = pyqtSignal(bytes)
    _state_sig = pyqtSignal(str)
    _setup_sig = pyqtSignal(str, int)
    _remote_start_signal = pyqtSignal()
    _remote_stop_signal = pyqtSignal()
    _proactive_toggle_signal = pyqtSignal(bool)
    _qr_signal = pyqtSignal(bytes, str, str)

    def show_qr_code(self, qr_buf: io.BytesIO, url: str, password: str = ""):
        self._qr_signal.emit(qr_buf.getvalue(), url, password)

    def _show_qr_dialog(self, image_bytes: bytes, url: str, password: str = ""):
        dlg = QDialog(self)
        dlg.setWindowTitle("Remote Access")
        dlg.setFixedSize(380, 480)
        layout = QVBoxLayout(dlg)
        pix = QPixmap(); pix.loadFromData(image_bytes)
        lbl = QLabel(); lbl.setPixmap(pix.scaled(250, 250, Qt.AspectRatioMode.KeepAspectRatio))
        layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        url_label = QLabel(f"URL:\n{url}")
        url_label.setFont(QFont("Segoe UI", 7))
        url_label.setWordWrap(True)
        url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(url_label)
        pwd_label = QLabel(f"Password: {password}")
        pwd_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        pwd_label.setWordWrap(True)
        pwd_label.setStyleSheet("color: #00E6A0;")
        pwd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(pwd_label)
        dlg.show()

    def _toggle_interrupt(self):
        self._log.append_log("SYS: ⏹ HARD RESET requested.")
        self._apply_state("RESTARTING")
        self._interrupt_btn.setEnabled(False)
        if self._hard_reset_callback:
            try:
                threading.Thread(target=self._hard_reset_callback, daemon=True).start()
            except Exception as e:
                self._log.append_log(f"ERR: Hard reset failed: {e}")
                self._interrupt_btn.setEnabled(True)
                self._apply_state("LISTENING")
        else:
            self._log.append_log("ERR: Hard reset callback not set.")
            self._interrupt_btn.setEnabled(True)
            self._apply_state("LISTENING")

    def reset_complete(self):
        self._interrupt_btn.setEnabled(True)
        self._muted = False
        self.hud.muted = False
        self._style_mute_btn()
        self._apply_state("LISTENING")
        self._log.append_log("SYS: Microphone active.")

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S — Just A Rather Very Intelligent System")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move((screen.width() - _DEFAULT_W)//2, (screen.height() - _DEFAULT_H)//2)

        self._qr_signal.connect(self._show_qr_dialog)
        self._remote_active = False
        self.on_text_command = None
        self._muted = False
        self._current_file = None
        self._interrupt_flag = threading.Event()
        self._hard_reset_callback = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        self._cam_preview = QLabel(self)
        self._cam_preview.hide()
        self._cam_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_preview.setStyleSheet(f"background: {C.BG}; border: 2px solid {C.PRI}; border-radius: 8px;")
        self._cam_preview.setFixedSize(400, 300)
        self._cam_preview.setWindowFlags(Qt.WindowType.ToolTip)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        root.addLayout(self._build_body(), stretch=1)
        root.addWidget(self._build_bottom_bar())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._camera_preview_signal.connect(self._show_camera_preview_dialog)
        self._setup_sig.connect(self._update_setup_progress)

        self._overlay = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

    # ------------------------------------------------------------------
    # LAYOUT BUILDERS
    # ------------------------------------------------------------------
    def _build_header(self):
        w = QWidget()
        w.setFixedHeight(44)
        w.setStyleSheet("background: transparent; border: none;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        # Centered title + subtitle
        title = QLabel("J.A.R.V.I.S")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Just A Rather Very Intelligent System")
        sub.setFont(QFont("Segoe UI", 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.addWidget(title)
        title_col.addWidget(sub)

        lay.addStretch(1)
        lay.addLayout(title_col, 0)
        lay.addStretch(1)

        # Clock / date on right
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Segoe UI", 8))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        clock_col = QVBoxLayout()
        clock_col.setSpacing(0)
        clock_col.addWidget(self._clock_lbl, alignment=Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._date_lbl, alignment=Qt.AlignmentFlag.AlignRight)

        lay.addLayout(clock_col, 0)
        return w

    def _build_body(self):
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, 0)

        self.hud = HudCanvas("face.png")
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.hud, 1)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, 0)

        return body

    def _build_left_panel(self):
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: rgba(10, 16, 28, 0.65); border: 1px solid {C.BORDER}; border-radius: 16px;")
        _add_shadow(w, radius=24, alpha=60, offset=(0, 6))

        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel("SYSTEM VITALS")
        hdr.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")

        lay.addWidget(hdr)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#FF6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        info_panel = QWidget()
        info_panel.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 8px;")
        ip_lay = QVBoxLayout(info_panel); ip_lay.setContentsMargins(8, 6, 8, 6); ip_lay.setSpacing(2)
        self._uptime_lbl = QLabel("UP --:--")
        self._uptime_lbl.setFont(QFont("Segoe UI", 8)); self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._proc_lbl = QLabel("PROC --")
        self._proc_lbl.setFont(QFont("Segoe UI", 8)); self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS {os_name}")
        os_lbl.setFont(QFont("Segoe UI", 8)); os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        ip_lay.addWidget(self._uptime_lbl); ip_lay.addWidget(self._proc_lbl); ip_lay.addWidget(os_lbl)
        lay.addWidget(info_panel)
        lay.addStretch()

        self._remote_btn = QPushButton("REMOTE ACCESS")
        self._remote_btn.setFixedHeight(32)
        self._remote_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        self._remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remote_btn.clicked.connect(self._toggle_remote_access)
        self._update_remote_btn()
        lay.addWidget(self._remote_btn)

        self._proactive_enabled = False
        self._proactive_btn = QPushButton("PROACTIVE: OFF")
        self._proactive_btn.setFixedHeight(32)
        self._proactive_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        self._proactive_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proactive_btn.clicked.connect(self._toggle_proactive)
        self._style_proactive_btn()
        lay.addWidget(self._proactive_btn)

        return w

    def _build_right_panel(self):
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: rgba(10, 16, 28, 0.65); border: 1px solid {C.BORDER}; border-radius: 16px;")
        _add_shadow(w, radius=24, alpha=60, offset=(0, 6))

        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel("ACTIVITY LOG")
        hdr.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        
        lay.addWidget(hdr)

        self._log = LogWidget()
        lay.addWidget(self._log, 1)

        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded")
        self._file_hint.setFont(QFont("Segoe UI", 8))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        return w

    def _build_bottom_bar(self):
        w = QWidget()
        w.setFixedHeight(_BOTTOM_H)
        w.setStyleSheet(f"background: rgba(10, 16, 28, 0.72); border: 1px solid {C.BORDER}; border-radius: 16px;")
        _add_shadow(w, radius=28, alpha=70, offset=(0, 6))

        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(10)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Segoe UI", 9))
        self._input.setStyleSheet(f"""
            QLineEdit {{ background: #0A101C; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 10px; }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        lay.addWidget(self._input, 1)

        send = QPushButton("▸")
        send.setFixedSize(34, 34)
        send.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 6px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        lay.addWidget(send)

        self._mute_btn = QPushButton("🎙")
        self._mute_btn.setFixedSize(34, 34)
        self._mute_btn.setFont(QFont("Segoe UI", 12))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        fs_btn = QPushButton("⛶")
        fs_btn.setFixedSize(34, 34)
        fs_btn.setFont(QFont("Segoe UI", 12))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 6px; }}
            QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        self._interrupt_btn = QPushButton("⏹")
        self._interrupt_btn.setFixedSize(34, 34)
        self._interrupt_btn.setFont(QFont("Segoe UI", 12))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{ background: #140006; color: {C.RED}; border: 1px solid {C.RED}; border-radius: 6px; }}
            QPushButton:hover {{ background: #1f000a; }}
        """)
        self._interrupt_btn.clicked.connect(self._toggle_interrupt)
        lay.addWidget(self._interrupt_btn)

        return w


    # ------------------------------------------------------------------
    # TOGGLE AND UI STATE
    # ------------------------------------------------------------------
    def _toggle_remote_access(self):
        if self._remote_active:
            from server import stop_ngrok
            stop_ngrok()
            self._remote_active = False
            self._log.append_log("SYS: Remote tunnel closed.")
            self._update_remote_btn()
        else:
            from server import start_ngrok, generate_qr
            self._log.append_log("SYS: Starting remote tunnel...")
            url = start_ngrok()
            if url:
                self._remote_active = True
                config_path = Path(__file__).resolve().parent / "config" / "api_keys.json"
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    pwd = cfg.get("remote_password", "not set")
                except Exception:
                    pwd = "not set"
                qr_buf = generate_qr(url)
                self._log.append_log(f"SYS: Remote URL: {url}")
                self.show_qr_code(qr_buf, url, pwd)
                self._update_remote_btn()
            else:
                self._log.append_log("ERR: Could not start remote tunnel.")

    def _toggle_proactive(self):
        self._proactive_enabled = not self._proactive_enabled
        self._style_proactive_btn()
        self._proactive_toggle_signal.emit(self._proactive_enabled)
        self._log.append_log(f"SYS: Proactive assistance {'enabled' if self._proactive_enabled else 'disabled'}.")

    def _style_proactive_btn(self):
        if self._proactive_enabled:
            self._proactive_btn.setText("PROACTIVE: ON")
            self._proactive_btn.setStyleSheet(f"""
                QPushButton {{ background: #00140a; color: {C.GREEN}; border: 1px solid {C.GREEN}; border-radius: 6px; padding: 6px; }}
                QPushButton:hover {{ background: #002215; }}
            """)
        else:
            self._proactive_btn.setText("PROACTIVE: OFF")
            self._proactive_btn.setStyleSheet(f"""
                QPushButton {{ background: #140006; color: {C.RED}; border: 1px solid {C.RED}; border-radius: 6px; padding: 6px; }}
                QPushButton:hover {{ background: #1f000a; }}
            """)

    def _update_remote_btn(self):
        if self._remote_active:
            self._remote_btn.setText("DISCONNECT")
            self._remote_btn.setStyleSheet(f"""
                QPushButton {{ background: #140006; color: {C.RED}; border: 1px solid {C.RED}; border-radius: 6px; padding: 6px; }}
                QPushButton:hover {{ background: #1f000a; }}
            """)
        else:
            self._remote_btn.setText("REMOTE ACCESS")
            self._remote_btn.setStyleSheet(f"""
                QPushButton {{ background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 6px; padding: 6px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def show_camera_preview(self, image_bytes: bytes):
        self._camera_preview_signal.emit(image_bytes)

    def _show_camera_preview_dialog(self, image_bytes: bytes):
        try:
            pixmap = QPixmap(); pixmap.loadFromData(image_bytes)
            if pixmap.isNull(): return
            self._cam_preview.setPixmap(pixmap.scaled(400, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self._cam_preview.move(self.geometry().center() - self._cam_preview.rect().center())
            self._cam_preview.show()
            self._cam_preview.raise_()
            QTimer.singleShot(4000, self._cam_preview.hide)
        except Exception as e:
            print(f"[UI] ⚠️ Camera preview error: {e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            ow, oh = 500, 420
            cw = self.centralWidget()
            self._overlay.setGeometry((cw.width()-ow)//2, (cw.height()-oh)//2, ow, oh)

    def _update_metrics(self):
        snap = _metrics.snapshot()
        self._bar_cpu.set_value(snap["cpu"], f"{snap['cpu']:.0f}%")
        self._bar_mem.set_value(snap["mem"], f"{snap['mem']:.0f}%")
        net = snap["net"]
        if net < 1.0: net_str = f"{net*1024:.0f}KB/s"
        else: net_str = f"{net:.1f}MB/s"
        self._bar_net.set_value(min(100, net*10), net_str)
        self._bar_gpu.set_value(snap["gpu"] if snap["gpu"]>=0 else 0, f"{snap['gpu']:.0f}%" if snap["gpu"]>=0 else "N/A")
        self._bar_tmp.set_value(min(100, snap["tmp"]) if snap["tmp"]>=0 else 0, f"{snap['tmp']:.0f}°C" if snap["tmp"]>=0 else "N/A")
        try:
            boot_t = psutil.boot_time(); elapsed = time.time()-boot_t
            h, m = int(elapsed//3600), int((elapsed%3600)//60)
            self._uptime_lbl.setText(f"UP {h:02d}:{m:02d}")
        except: self._uptime_lbl.setText("UP --:--")
        try: self._proc_lbl.setText(f"PROC {len(psutil.pids())}")
        except: self._proc_lbl.setText("PROC --")

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _update_setup_progress(self, text: str, percent: int):
        try:
            if percent is None:
                self._setup_label.hide(); self._setup_progress.hide(); return
            self._setup_label.setText(text)
            self._setup_progress.setValue(max(0, min(100, percent)))
            self._setup_label.show(); self._setup_progress.show()
        except Exception: pass

    def _on_file_selected(self, path: str):
        self._current_file = path
        p = Path(path); cat = _file_category(p); icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"]); size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon} {p.name} · {size} · Tell JARVIS what to do")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = f"[FILE_UPLOADED] path={path} | name={p.name} | type={p.suffix.lstrip('.')} | size={size} | Briefly tell the user you can see the file '{p.name}' ({size}) has been uploaded and ask what they'd like to do with it."
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED"); self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING"); self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{ background: #140006; color: {C.MUTED_C}; border: 1px solid {C.MUTED_C}; border-radius: 6px; }}
            """)
        else:
            self._mute_btn.setText("🎙")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{ background: #00140a; color: {C.GREEN}; border: 1px solid {C.GREEN}; border-radius: 6px; }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("openrouter_api_key")) and bool(d.get("os_system"))
        except: return False

    def _is_online(self, timeout=3):
        try:
            requests.get("https://www.google.com/generate_204", timeout=timeout)
            return True
        except: return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 500, 420
        ov.setGeometry((cw.width()-ow)//2, (cw.height()-oh)//2, ow, oh)
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, or_key: str, os_name: str):
        import os, json
        os.makedirs(CONFIG_DIR, exist_ok=True)
        existing = {}
        if API_FILE.exists():
            try: existing = json.loads(API_FILE.read_text(encoding="utf-8"))
            except: pass
        existing["gemini_api_key"] = key
        existing["openrouter_api_key"] = or_key
        existing["os_system"] = os_name
        API_FILE.write_text(json.dumps(existing, indent=4), encoding="utf-8")
        self._ready = True
        if self._overlay:
            self._overlay.hide(); self._overlay = None
        self._apply_state("LISTENING")
        if self._is_online():
            self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")
        else:
            self._apply_state("Offline mode – local only")
            self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. Offline mode – local only.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_): pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    def show_qr_code(self, qr_buf: io.BytesIO, url: str):
        self._win.show_qr_code(qr_buf, url)

    @property
    def interrupt_flag(self) -> threading.Event:
        return self._win._interrupt_flag

    @property
    def on_hard_reset(self):
        return self._win._hard_reset_callback

    @on_hard_reset.setter
    def on_hard_reset(self, cb):
        self._win._hard_reset_callback = cb

    def reset_complete(self):
        self._win.reset_complete()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")


