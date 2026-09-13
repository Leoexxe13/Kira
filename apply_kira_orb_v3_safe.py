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

backup = ROOT / "ui.py.pre_kira_orb_v3"
if not backup.exists():
    shutil.copy2(UI, backup)
    print("✅ Backup creado:", backup.name)

text = UI.read_text(encoding="utf-8")

start = text.find("# === KIRA_CORE_V2_SAFE ===")
end = text.find("class MetricBar(QWidget):", start)

if start == -1 or end == -1:
    print("❌ No encontré KIRA Core v2 en ui.py.")
    print("Asegúrate de haber aplicado apply_kira_core_v2_safe.py primero.")
    sys.exit(1)

new_core = r'''# === KIRA_CORE_ORB_V3 ===
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

        # Technical grid.
        p.setPen(QPen(qcol(C.BORDER, 50), 1))
        step = max(38, int(base * 0.09))
        for x in range(0, W, step):
            p.drawLine(x, 0, x, H)
        for y in range(0, H, step):
            p.drawLine(0, y, W, y)

        # Crosshair.
        p.setPen(QPen(qcol(C.PRI, 45), 1))
        p.drawLine(QPointF(cx, cy - R * 1.55), QPointF(cx, cy + R * 1.55))
        p.drawLine(QPointF(cx - R * 1.55, cy), QPointF(cx + R * 1.55, cy))

        amp = max(0.0, min(1.0, getattr(self, "_amp_disp", 0.0)))
        pulse = 1.0 + amp * 0.10 + math.sin(self._tick * 0.035) * 0.012

        # Outer soft halo.
        for i in range(4):
            rr = R * (1.02 + i * 0.075) * pulse
            p.setPen(QPen(qcol(C.PRI, max(8, 30 - i * 6)), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # Thin concentric rings.
        for frac, alpha, width in (
            (0.58, 95, 1.0),
            (0.74, 135, 1.0),
            (0.90, 175, 1.2),
            (1.08, 110, 1.0),
        ):
            rr = R * frac
            p.setPen(QPen(qcol(C.PRI, alpha), width))
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # Moving segmented arcs.
        rings = getattr(self, "_rings", [0.0, 120.0, 240.0])
        for frac, phase, span, gap, width in (
            (0.72, rings[0], 42, 32, 1.8),
            (0.90, rings[1], 28, 24, 1.3),
            (1.08, rings[2], 18, 18, 1.0),
        ):
            rr = R * frac
            rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(qcol(C.PRI, 205), width))
            a = phase
            while a < phase + 360:
                p.drawArc(rect, int(a * 16), int(span * 16))
                a += span + gap

        # Rotating radar sweep.
        scan = getattr(self, "_scan", 0.0)
        rr = R * 1.18
        rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
        p.setPen(QPen(qcol(C.ACC2, 125), 2.0))
        p.drawArc(rect, int(scan * 16), int(24 * 16))

        # Outer ticks.
        p.setPen(QPen(qcol(C.TEXT_MED, 130), 1))
        for deg in range(0, 360, 6):
            rad = math.radians(deg)
            outer = R * 1.22
            inner = R * (1.15 if deg % 30 == 0 else 1.19)
            p.drawLine(
                QPointF(cx + math.cos(rad) * inner, cy + math.sin(rad) * inner),
                QPointF(cx + math.cos(rad) * outer, cy + math.sin(rad) * outer),
            )

        # Orbiting light nodes.
        for i in range(12):
            speed = 0.20 + (i % 4) * 0.03
            ang = math.radians((self._tick * speed + i * 30) % 360)
            orbit = R * (0.74 + (i % 3) * 0.13)
            x = cx + math.cos(ang) * orbit
            y = cy + math.sin(ang) * orbit
            pr = 1.7 if i % 4 else 3.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcol(C.PRI, 200 if i % 4 else 255))
            p.drawEllipse(QRectF(x - pr, y - pr, pr * 2, pr * 2))

        # Futuristic living orb.
        orb_r = R * 0.30 * pulse
        phase = math.radians((self._tick * 0.9) % 360)

        # Orb glow shells.
        for i in range(5):
            rr = orb_r * (1.05 + i * 0.11)
            p.setPen(QPen(qcol(C.PRI, max(10, 58 - i * 10)), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # Orb body.
        p.setPen(QPen(qcol(C.WHITE, 225), 1.3))
        p.setBrush(qcol(C.PANEL2, 245))
        p.drawEllipse(QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2))

        # Rotating wireframe longitude curves.
        p.setPen(QPen(qcol(C.PRI, 120), 1))
        for i in range(7):
            offset = (i - 3) / 3.0
            squeeze = max(0.14, abs(math.cos(phase + offset * 0.7)))
            shift = math.sin(phase + offset * 0.8) * orb_r * 0.10
            w = orb_r * 2 * squeeze
            p.drawEllipse(QRectF(
                cx - w / 2 + shift,
                cy - orb_r,
                w,
                orb_r * 2
            ))

        # Rotating latitude curves.
        for i in range(5):
            offset = (i - 2) / 2.0
            squeeze = max(0.16, abs(math.sin(phase * 0.8 + offset * 0.7)))
            shift = math.cos(phase + offset) * orb_r * 0.08
            h = orb_r * 2 * squeeze
            p.drawEllipse(QRectF(
                cx - orb_r,
                cy - h / 2 + shift,
                orb_r * 2,
                h
            ))

        # Dynamic internal arcs for motion.
        p.setPen(QPen(qcol(C.WHITE, 160), 1.2))
        inner_rect = QRectF(
            cx - orb_r * 0.82,
            cy - orb_r * 0.82,
            orb_r * 1.64,
            orb_r * 1.64
        )
        p.drawArc(inner_rect, int((self._tick * 1.6) % 360 * 16), int(110 * 16))
        p.drawArc(inner_rect, int((-self._tick * 1.1) % 360 * 16), int(80 * 16))

        # Bright breathing center.
        dot_r = 3.5 + amp * 5.0 + (math.sin(self._tick * 0.08) + 1.0) * 0.8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(C.PRI, 255))
        p.drawEllipse(QRectF(cx - dot_r, cy - dot_r, dot_r * 2, dot_r * 2))

        # State under orb; no static KIRA name inside.
        state_txt = str(getattr(self, "state", "READY")).upper()
        p.setPen(qcol(C.TEXT_MED, 220))
        p.setFont(QFont("Courier New", max(7, int(base * 0.011)), QFont.Weight.Bold))
        p.drawText(
            QRectF(cx - R, cy + R * 1.38, R * 2, 20),
            int(Qt.AlignmentFlag.AlignCenter),
            state_txt
        )

        # Reactive waveform.
        wave_y = cy + R * 1.57
        wave_w = R * 1.30
        p.setPen(QPen(qcol(C.PRI, 120), 1))
        for i in range(34):
            rel = i / 33.0
            x = cx - wave_w / 2 + rel * wave_w
            wobble = math.sin(self._tick * 0.15 + i * 0.75)
            h = 3 + (7 + amp * 18) * abs(wobble)
            p.drawLine(QPointF(x, wave_y - h / 2), QPointF(x, wave_y + h / 2))

        p.end()


'''
text = text[:start] + new_core + text[end:]
UI.write_text(text, encoding="utf-8")

try:
    py_compile.compile(str(UI), doraise=True)
except Exception as e:
    print("❌ El ui.py resultante no compila:")
    print(e)
    shutil.copy2(backup, UI)
    print("↩️ Restaurado automáticamente.")
    sys.exit(1)

print("")
print("✅ KIRA Orb v3 aplicado y validado")
print("• Quitado el nombre estático del centro")
print("• Orb wireframe animado")
print("• Longitudes y latitudes en movimiento")
print("• Arcos internos giratorios")
print("• Centro reactivo al audio")
print("• Se conserva el radar y los anillos exteriores")
print("")
print("Ejecuta:")
print("    python main.py")
print("")
print("Restaurar:")
print("    cp ui.py.pre_kira_orb_v3 ui.py")
