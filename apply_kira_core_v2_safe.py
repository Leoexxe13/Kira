#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys
import py_compile

ROOT = Path.cwd()
UI = ROOT / "ui.py"

if not UI.exists():
    print("❌ No encuentro ui.py. Ejecuta esto desde ~/Mark-LIII")
    sys.exit(1)

backup = ROOT / "ui.py.pre_kira_core_v2"
if not backup.exists():
    shutil.copy2(UI, backup)
    print("✅ Backup creado:", backup.name)

text = UI.read_text(encoding="utf-8")

MARKER = "# === KIRA_CORE_V2_SAFE ==="
if MARKER in text:
    print("ℹ️ KIRA Core v2 ya está aplicado.")
    sys.exit(0)

metric_anchor = "class MetricBar(QWidget):"
if metric_anchor not in text:
    print("❌ No encontré MetricBar. No se cambió ui.py.")
    sys.exit(1)

core_class = r'''
# === KIRA_CORE_V2_SAFE ===
class KiraCoreV2(HudCanvas):
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2.0, H / 2.0
        base = min(W, H)
        R = base * 0.29

        p.setPen(QPen(qcol(C.BORDER, 55), 1))
        step = max(36, int(base * 0.085))
        for x in range(0, W, step):
            p.drawLine(x, 0, x, H)
        for y in range(0, H, step):
            p.drawLine(0, y, W, y)

        p.setPen(QPen(qcol(C.PRI, 48), 1))
        p.drawLine(QPointF(cx, cy - R * 1.5), QPointF(cx, cy + R * 1.5))
        p.drawLine(QPointF(cx - R * 1.5, cy), QPointF(cx + R * 1.5, cy))

        amp = max(0.0, min(1.0, getattr(self, "_amp_disp", 0.0)))
        pulse = 1.0 + (amp * 0.10) + math.sin(self._tick * 0.035) * 0.012

        for i in range(5):
            rr = R * (1.00 + i * 0.07) * pulse
            p.setPen(QPen(qcol(C.PRI, max(8, 34 - i * 6)), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        for frac, alpha, width in (
            (0.50, 75, 1.0),
            (0.66, 125, 1.0),
            (0.82, 165, 1.2),
            (1.00, 190, 1.1),
            (1.18, 100, 1.0),
        ):
            rr = R * frac
            p.setPen(QPen(qcol(C.PRI, alpha), width))
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        rings = getattr(self, "_rings", [0.0, 120.0, 240.0])
        for frac, phase, span, gap, width in (
            (0.72, rings[0], 50, 38, 2.0),
            (0.90, rings[1], 34, 26, 1.4),
            (1.08, rings[2], 22, 18, 1.0),
        ):
            rr = R * frac
            rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(qcol(C.PRI, 205), width))
            angle = phase
            while angle < phase + 360:
                p.drawArc(rect, int(angle * 16), int(span * 16))
                angle += span + gap

        scan = getattr(self, "_scan", 0.0)
        rr = R * 1.22
        rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
        p.setPen(QPen(qcol(C.ACC2, 130), 2.2))
        p.drawArc(rect, int(scan * 16), int(26 * 16))

        p.setPen(QPen(qcol(C.TEXT_MED, 145), 1))
        for deg in range(0, 360, 6):
            rad = math.radians(deg)
            outer = R * 1.24
            inner = R * (1.16 if deg % 30 == 0 else 1.20)
            p.drawLine(
                QPointF(cx + math.cos(rad) * inner, cy + math.sin(rad) * inner),
                QPointF(cx + math.cos(rad) * outer, cy + math.sin(rad) * outer),
            )

        for i in range(10):
            ang = math.radians((self._tick * (0.22 + i * 0.015) + i * 36) % 360)
            orbit = R * (0.78 + (i % 3) * 0.14)
            x = cx + math.cos(ang) * orbit
            y = cy + math.sin(ang) * orbit
            pr = 2.0 if i % 4 else 3.4
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcol(C.PRI, 210 if i % 4 else 255))
            p.drawEllipse(QRectF(x - pr, y - pr, pr * 2, pr * 2))

        core_r = R * 0.33 * pulse
        p.setBrush(qcol(C.PANEL2, 245))
        p.setPen(QPen(qcol(C.WHITE, 230), 1.5))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        p.setPen(QPen(qcol(C.PRI, 95), 1))
        for squash in (0.38, 0.64):
            p.drawEllipse(QRectF(
                cx - core_r * squash, cy - core_r,
                core_r * squash * 2, core_r * 2
            ))
        for squash in (0.40, 0.68):
            p.drawEllipse(QRectF(
                cx - core_r, cy - core_r * squash,
                core_r * 2, core_r * squash * 2
            ))

        dot_r = 3.0 + amp * 4.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(C.PRI, 255))
        p.drawEllipse(QRectF(cx - dot_r, cy - dot_r, dot_r * 2, dot_r * 2))

        p.setPen(qcol(C.WHITE, 238))
        p.setFont(QFont("Courier New", max(10, int(base * 0.025)), QFont.Weight.Bold))
        p.drawText(
            QRectF(cx - core_r, cy - 12, core_r * 2, 24),
            int(Qt.AlignmentFlag.AlignCenter),
            "KIRA"
        )

        state_txt = str(getattr(self, "state", "READY")).upper()
        p.setPen(qcol(C.TEXT_MED, 215))
        p.setFont(QFont("Courier New", max(7, int(base * 0.011)), QFont.Weight.Bold))
        p.drawText(
            QRectF(cx - R, cy + R * 1.40, R * 2, 20),
            int(Qt.AlignmentFlag.AlignCenter),
            state_txt
        )

        wave_y = cy + R * 1.58
        wave_w = R * 1.34
        p.setPen(QPen(qcol(C.PRI, 120), 1))
        for i in range(34):
            rel = i / 33.0
            x = cx - wave_w / 2 + rel * wave_w
            wobble = math.sin(self._tick * 0.15 + i * 0.75)
            h = 3 + (7 + amp * 18) * abs(wobble)
            p.drawLine(QPointF(x, wave_y - h / 2), QPointF(x, wave_y + h / 2))

        p.end()


'''
text = text.replace(metric_anchor, core_class + metric_anchor, 1)

old = "        self.hud = HudCanvas(face_path, _display)\n"
new = "        self.hud = KiraCoreV2(face_path, _display)\n"

if old not in text:
    print("❌ No encontré la creación del HUD principal. No se cambió ui.py.")
    sys.exit(1)

text = text.replace(old, new, 1)
UI.write_text(text, encoding="utf-8")

try:
    py_compile.compile(str(UI), doraise=True)
except Exception as e:
    print("❌ El ui.py resultante no compila:")
    print(e)
    shutil.copy2(backup, UI)
    print("↩️ Restaurado automáticamente desde el backup.")
    sys.exit(1)

print("")
print("✅ KIRA Core v2 aplicado y validado")
print("• Núcleo más pequeño y fino")
print("• Anillos segmentados y radar suave")
print("• Nodos orbitales y centro wireframe")
print("• Reacciona al audio y al estado")
print("• La estructura general todavía no se toca")
print("")
print("Ejecuta:")
print("    python main.py")
print("")
print("Restaurar:")
print("    cp ui.py.pre_kira_core_v2 ui.py")
