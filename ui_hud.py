"""Lightweight Qt HUD components. No backend work or generated telemetry."""
from collections import deque
import math
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient
from PyQt6.QtWidgets import QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSizePolicy

STYLE = '''
QWidget { color:#dce1e7; font-family:"Helvetica Neue";  }
QFrame#HudPanel { background:#0c0e11; border:1px solid #41464e; border-radius:3px; }
QLabel { background:transparent; border:none; }
QPushButton { background:#12161b; color:#dce1e7; border:1px solid #454b55; border-radius:3px; padding:6px 9px; }
QPushButton:hover { background:#222a33; border-color:#b9c9dd; color:white; }
QPushButton:focus { border-color:#c9def5; }
QPushButton:disabled { color:#646b76; border-color:#272c33; }
QLineEdit, QTextEdit, QListWidget { background:#080b0e; color:#dce1e7; border:1px solid #353c45; border-radius:3px; padding:7px; selection-background-color:#38485a; }
QLineEdit:focus { border-color:#a6b9d0; }
QScrollArea { background:transparent; border:none; }
QScrollBar:vertical { background:#101318; width:6px; }
QScrollBar::handle:vertical { background:#59616c; min-height:24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
'''

def label(text, size=10, dim=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setFont(QFont('Menlo', size))
    w.setStyleSheet('color:#8994a3; border:none; background:transparent;' if dim else 'color:#e6eaf0; border:none; background:transparent;')
    w.setWordWrap(True)
    return w


class ProviderIcon(QWidget):
    """Small code-drawn provider marks; no font-dependent decorative glyphs."""
    def __init__(self, name, color):
        super().__init__()
        self.name, self.color = name, color
        self.setFixedSize(24,24)
        self.setAccessibleName(name)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.color),1.4))
        if self.name == 'Gemini':
            path = QPainterPath(QPointF(12,2))
            path.quadTo(13,11,22,12); path.quadTo(13,13,12,22)
            path.quadTo(11,13,2,12); path.quadTo(11,11,12,2)
            painter.fillPath(path,QColor(self.color))
        elif self.name == 'OpenAI':
            painter.translate(12,12)
            for _ in range(6):
                painter.drawRoundedRect(QRectF(-3,-10,8,13),3,3)
                painter.rotate(60)
        elif self.name == 'Local':
            painter.drawRect(QRectF(6,6,12,12))
            for v in (8,12,16):
                painter.drawLine(v,3,v,6); painter.drawLine(v,18,v,21)
                painter.drawLine(3,v,6,v); painter.drawLine(18,v,21,v)
        else:
            painter.setFont(QFont('Helvetica Neue',16,QFont.Weight.Bold))
            painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'g' if self.name=='Groq' else 'A')
        painter.end()


class ProviderList(QWidget):
    def __init__(self):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0,0,0,0)
        self.body.setSpacing(6)
        self.rows = {}
        self.network = label('Consultando configuración…',8,True)
        self.body.addWidget(self.network)

    def update_providers(self, records, network):
        present = {data[2] for data in records}
        for name, (widget, _, _, _) in self.rows.items():
            widget.setVisible(name in present)
        for _, color, name, model, capabilities, status in records:
            if name not in self.rows:
                widget = QWidget()
                outer = QHBoxLayout(widget); outer.setContentsMargins(0,0,0,0)
                outer.addWidget(ProviderIcon(name,color))
                detail = QVBoxLayout(); detail.setContentsMargins(0,0,0,0); detail.setSpacing(2)
                top = QHBoxLayout()
                title = label(name,9)
                state = label('',8,True)
                state.setAlignment(Qt.AlignmentFlag.AlignRight)
                top.addWidget(title,1); top.addWidget(state)
                subtitle = label('',8,True)
                detail.addLayout(top); detail.addWidget(subtitle)
                outer.addLayout(detail,1)
                self.body.insertWidget(self.body.count()-1,widget)
                self.rows[name] = widget,title,subtitle,state
            widget,title,subtitle,state = self.rows[name]
            widget.show()
            title.setText(str(name)); subtitle.setText(f'{model} · {capabilities}'); state.setText(str(status))
        self.network.setText('Red: ' + str(network))

    def show_error(self):
        for widget, *_ in self.rows.values():widget.hide()
        self.network.setText('Estado de proveedores no disponible')

class Panel(QFrame):
    def __init__(self, title, badge='', parent=None):
        super().__init__(parent)
        self.setObjectName('HudPanel')
        self.setStyleSheet(STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(10, 9, 10, 10)
        self.body.setSpacing(8)
        h = QHBoxLayout()
        self.title = label(title, 9)
        self.title.setStyleSheet("font-family:Menlo; font-size:11px; font-weight:600; color:#e6eaf0; border:none;")
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        h.addWidget(self.title, 1)
        self.badge = label(badge, 8, True)
        h.addWidget(self.badge)
        self.body.addLayout(h)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QPen(QColor('#b1bfce'), 1))
        for x, y, dx, dy in ((1,1,1,1), (self.width()-2,1,-1,1), (1,self.height()-2,1,-1), (self.width()-2,self.height()-2,-1,-1)):
            p.drawLine(x,y,x+dx*8,y)
            p.drawLine(x,y,x,y+dy*8)

class Sparkline(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque(maxlen=60)
        self.setMinimumHeight(23)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    def sample(self, value):
        if value is not None and math.isfinite(float(value)):
            self.values.append(float(value))
            self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor('#252c35'), 1))
        p.drawLine(0, h-2, w, h-2)
        if len(self.values) < 2:
            return
        peak = max(max(self.values), 1)
        path = QPainterPath()
        for i, v in enumerate(self.values):
            point = QPointF(w * i / 59, h-3 - (h-7)*max(0, v)/peak)
            if i == 0: path.moveTo(point)
            else: path.lineTo(point)
        p.setPen(QPen(QColor('#c5d5e8'), 1.1)); p.drawPath(path)

class Terrain(QWidget):
    """Decorative wire surface, explicitly not a data or microphone chart."""
    def __init__(self):
        super().__init__()
        self.phase = 0
        self.setMinimumHeight(65)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
    def showEvent(self, event):
        self.timer.start(65)
    def hideEvent(self, event):
        self.timer.stop()
    def advance(self):
        self.phase += .045
        self.update()
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        for row in range(12):
            path = QPainterPath()
            depth = row/11
            for col in range(45):
                x = col/44
                wave = math.sin(x*11+self.phase+depth*3)*math.exp(-((x-.55)*3)**2)
                pt = QPointF(w*(.08*(1-depth)+x*(.84+.16*depth)), h*(.22+.65*depth)-wave*h*.17)
                if col == 0: path.moveTo(pt)
                else: path.lineTo(pt)
            p.setPen(QPen(QColor(175,194,216,int(40+110*depth)), .8)); p.drawPath(path)
        p.setPen(QColor('#738091')); p.setFont(QFont('Menlo',7))
        p.drawText(4,h-3,'CONTEXT FIELD  /  VISUALIZACIÓN AMBIENTAL')
