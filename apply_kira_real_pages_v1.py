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

backup = ROOT / "ui.py.pre_kira_pages_v1"
if not backup.exists():
    shutil.copy2(UI, backup)
    print("✅ Backup creado:", backup.name)

text = UI.read_text(encoding="utf-8")

MARKER = "# === KIRA_REAL_PAGES_V1 ==="
if MARKER in text:
    print("ℹ️ KIRA Real Pages v1 ya está aplicado.")
    sys.exit(0)

# We require the current safe orb/core base.
if "class KiraCoreV2(HudCanvas):" not in text:
    print("❌ No encuentro KiraCoreV2. Aplica primero el parche del orbe/core.")
    sys.exit(1)

# ------------------------------------------------------------------
# 1) Replace only the old single body layout with a real page stack.
#    Everything before/after remains untouched.
# ------------------------------------------------------------------
start = text.find("        body = QHBoxLayout()\n")
end_line = "        root.addLayout(body, stretch=1)\n"
end = text.find(end_line, start)

if start == -1 or end == -1:
    print("❌ No pude localizar el body principal actual.")
    sys.exit(1)

end += len(end_line)

new_body = r'''        # === KIRA_REAL_PAGES_V1 ===
        # Build original functional widgets first; then place them on distinct pages.
        self._left_panel = self._build_left_panel()
        self._left_panel.setMinimumWidth(230)
        self._left_panel.setMaximumWidth(310)

        # KIRA core + content/news/briefing panel.
        self.hud = KiraCoreV2(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Live camera container — keeps original camera behavior.
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #020304;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(10, 7, 10, 7)
        _cam_title = QLabel("CAMERA // LIVE")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)

        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(
            f"QSplitter::handle {{ background:{C.BORDER}; height:4px; }}"
            f"QSplitter::handle:hover {{ background:{C.PRI_DIM}; }}"
        )
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 2)
        self._center_split.setCollapsible(0, False)
        self._center_split.setSizes([430, 250])

        # Original conversation/file/command widgets become the real CHAT page.
        self._right_panel = self._build_right_panel()
        self._right_panel.setMinimumWidth(0)
        self._right_panel.setMaximumWidth(16777215)
        self._right_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # The page stack makes the top menu true navigation instead of resizing one screen.
        self._workspace_stack = QStackedWidget()
        self._workspace_stack.setStyleSheet(
            f"background:{C.BG}; border:none;"
        )

        self._home_page = self._build_real_home_page()
        self._system_page = self._build_real_system_page()

        self._kira_page = QWidget()
        _kira_layout = QVBoxLayout(self._kira_page)
        _kira_layout.setContentsMargins(12, 10, 12, 10)
        _kira_layout.setSpacing(8)
        _kira_title = QLabel("KIRA // CORE")
        _kira_title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        _kira_title.setStyleSheet(
            f"color:{C.WHITE}; background:transparent; letter-spacing:2px;"
        )
        _kira_layout.addWidget(_kira_title)
        _kira_layout.addWidget(self._center_split, 1)

        self._tasks_page = self._build_real_tasks_page()

        self._chat_page = QWidget()
        _chat_layout = QVBoxLayout(self._chat_page)
        _chat_layout.setContentsMargins(12, 10, 12, 10)
        _chat_layout.setSpacing(8)
        _chat_title = QLabel("CHAT // CONVERSATION")
        _chat_title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        _chat_title.setStyleSheet(
            f"color:{C.WHITE}; background:transparent; letter-spacing:2px;"
        )
        _chat_layout.addWidget(_chat_title)
        _chat_layout.addWidget(self._right_panel, 1)

        for _page in (
            self._home_page,
            self._system_page,
            self._kira_page,
            self._tasks_page,
            self._chat_page,
        ):
            self._workspace_stack.addWidget(_page)

        root.addWidget(self._workspace_stack, 1)
        self._workspace_stack.setCurrentIndex(0)
'''

text = text[:start] + new_body + text[end:]

# ------------------------------------------------------------------
# 2) Add builders and override _app_view later in the class.
# ------------------------------------------------------------------
anchor = "    def _show_camera_frame(self, img_bytes: bytes):\n"
insert_at = text.find(anchor)

if insert_at == -1:
    print("❌ No encontré _show_camera_frame para insertar las páginas.")
    shutil.copy2(backup, UI)
    sys.exit(1)

methods = r'''    def _kira_section_title(self, title: str) -> QLabel:
        lab = QLabel(title)
        lab.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        lab.setStyleSheet(
            f"color:{C.WHITE}; background:transparent; letter-spacing:1px;"
        )
        return lab

    def _kira_metric_box(self, name: str):
        box = QWidget()
        box.setStyleSheet(
            f"background:{C.PANEL}; border:1px solid {C.BORDER}; border-radius:4px;"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        n = QLabel(name)
        n.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        n.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")

        value = QLabel("--")
        value.setFont(QFont("Courier New", 15, QFont.Weight.Bold))
        value.setStyleSheet(f"color:{C.WHITE}; background:transparent;")

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(7)
        bar.setStyleSheet(
            f"QProgressBar {{ background:{C.BAR_BG}; border:none; }}"
            f"QProgressBar::chunk {{ background:{C.PRI}; }}"
        )

        lay.addWidget(n)
        lay.addWidget(value)
        lay.addWidget(bar)
        return box, value, bar

    def _build_real_home_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        # LEFT — compact live system panel.
        left = QWidget()
        left.setStyleSheet(f"background:{C.DARK}; border:1px solid {C.BORDER};")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(8)
        lv.addWidget(self._kira_section_title("SYSTEM // LIVE"))

        c, self._home_cpu, self._home_cpu_bar = self._kira_metric_box("CPU")
        lv.addWidget(c)
        c, self._home_ram, self._home_ram_bar = self._kira_metric_box("RAM")
        lv.addWidget(c)
        c, self._home_net, self._home_net_bar = self._kira_metric_box("NETWORK")
        lv.addWidget(c)
        c, self._home_temp, self._home_temp_bar = self._kira_metric_box("TEMP")
        lv.addWidget(c)
        lv.addStretch()
        outer.addWidget(left, 3)

        # CENTER — a separate compact live KIRA orb and useful briefing.
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        core_box = QWidget()
        core_box.setStyleSheet(f"background:{C.DARK}; border:1px solid {C.BORDER};")
        core_l = QVBoxLayout(core_box)
        core_l.setContentsMargins(8, 8, 8, 8)
        core_l.addWidget(self._kira_section_title("KIRA // INTELLIGENCE"))
        self._home_core = KiraCoreV2(self._face_path, self._assistant_name.upper())
        self._home_core.setMinimumHeight(300)
        core_l.addWidget(self._home_core, 1)
        cv.addWidget(core_box, 5)

        brief_box = QWidget()
        brief_box.setStyleSheet(f"background:{C.DARK}; border:1px solid {C.BORDER};")
        brief_l = QVBoxLayout(brief_box)
        brief_l.setContentsMargins(10, 8, 10, 8)
        brief_l.addWidget(self._kira_section_title("BRIEFING // INTELLIGENCE"))
        self._home_briefing = QTextEdit()
        self._home_briefing.setReadOnly(True)
        self._home_briefing.setFrameShape(QFrame.Shape.NoFrame)
        self._home_briefing.setFont(QFont("Courier New", 8))
        self._home_briefing.setStyleSheet(
            f"background:transparent; color:{C.TEXT_MED}; border:none;"
        )
        self._home_briefing.setPlainText(
            "KIRA lista.\\n\\n"
            "• Esperando instrucciones.\\n"
            "• Noticias, búsquedas y briefings aparecerán aquí.\\n"
            "• Si una fuente no devuelve resultados, se mostrará contexto útil."
        )
        brief_l.addWidget(self._home_briefing, 1)
        cv.addWidget(brief_box, 2)

        outer.addWidget(center, 6)

        # RIGHT — recent activity.
        right = QWidget()
        right.setStyleSheet(f"background:{C.DARK}; border:1px solid {C.BORDER};")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 10, 10, 10)
        rv.setSpacing(8)
        rv.addWidget(self._kira_section_title("ACTIVITY // RECENT"))

        self._home_activity = QTextEdit()
        self._home_activity.setReadOnly(True)
        self._home_activity.setFrameShape(QFrame.Shape.NoFrame)
        self._home_activity.setFont(QFont("Courier New", 8))
        self._home_activity.setStyleSheet(
            f"background:transparent; color:{C.TEXT}; border:none;"
        )
        self._home_activity.setPlainText("KIRA online.\\nEsperando actividad...")
        rv.addWidget(self._home_activity, 1)

        self._home_state = QLabel("●  READY")
        self._home_state.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._home_state.setStyleSheet(
            f"color:{C.PRI}; background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; padding:8px;"
        )
        rv.addWidget(self._home_state)

        outer.addWidget(right, 4)
        return page

    def _build_real_system_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        # Reuse original live system panel.
        lay.addWidget(self._left_panel, 0)

        details = QWidget()
        details.setStyleSheet(
            f"background:{C.DARK}; border:1px solid {C.BORDER};"
        )
        dv = QVBoxLayout(details)
        dv.setContentsMargins(14, 12, 14, 12)
        dv.setSpacing(10)
        dv.addWidget(self._kira_section_title("SYSTEM // STATUS & PERFORMANCE"))

        row1 = QHBoxLayout()
        c, self._sys_cpu, self._sys_cpu_bar = self._kira_metric_box("CPU LOAD")
        row1.addWidget(c)
        c, self._sys_ram, self._sys_ram_bar = self._kira_metric_box("MEMORY")
        row1.addWidget(c)
        dv.addLayout(row1)

        row2 = QHBoxLayout()
        c, self._sys_net, self._sys_net_bar = self._kira_metric_box("NETWORK")
        row2.addWidget(c)
        c, self._sys_temp, self._sys_temp_bar = self._kira_metric_box("TEMPERATURE")
        row2.addWidget(c)
        dv.addLayout(row2)

        info = QTextEdit()
        info.setReadOnly(True)
        info.setFont(QFont("Courier New", 9))
        info.setStyleSheet(
            f"background:{C.PANEL}; color:{C.TEXT_MED}; "
            f"border:1px solid {C.BORDER}; padding:10px;"
        )
        info.setPlainText(
            "KIRA SYSTEM MONITOR\\n\\n"
            "Esta página está dedicada al estado del equipo.\\n"
            "Ya no comparte la misma vista con CHAT o TASKS."
        )
        dv.addWidget(info, 1)

        lay.addWidget(details, 1)
        return page

    def _build_real_tasks_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(self._kira_section_title("TASKS // ACTIVITY"))
        head.addStretch()

        for label in ("ALL", "RUNNING", "PENDING", "COMPLETED"):
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            btn.setStyleSheet(
                f"QPushButton {{ color:{C.TEXT_MED}; background:{C.PANEL}; "
                f"border:1px solid {C.BORDER}; padding:6px 10px; }}"
                f"QPushButton:hover {{ color:{C.WHITE}; border-color:{C.PRI_DIM}; }}"
            )
            head.addWidget(btn)

        lay.addLayout(head)

        desc = QLabel(
            "Vista independiente para tareas, procesos, cancelaciones y herramientas."
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Courier New", 8))
        desc.setStyleSheet(
            f"color:{C.TEXT_MED}; background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; padding:9px;"
        )
        lay.addWidget(desc)

        self._tasks_feed = QTextEdit()
        self._tasks_feed.setReadOnly(True)
        self._tasks_feed.setFont(QFont("Courier New", 9))
        self._tasks_feed.setStyleSheet(
            f"background:{C.DARK}; color:{C.TEXT}; "
            f"border:1px solid {C.BORDER}; padding:10px;"
        )
        self._tasks_feed.setPlainText(
            "TASK QUEUE READY\\n\\n"
            "No hay actividad registrada todavía.\\n"
            "Las acciones de KIRA se reflejarán aquí."
        )
        lay.addWidget(self._tasks_feed, 1)
        return page

    def _app_view(self, mode: str) -> None:
        pages = {
            "home": 0,
            "system": 1,
            "core": 2,
            "activity": 3,
            "chat": 4,
        }
        idx = pages.get(mode, 0)
        if hasattr(self, "_workspace_stack"):
            self._workspace_stack.setCurrentIndex(idx)

    def _update_kira_page_metrics(self) -> None:
        try:
            cpu = float(_metrics.cpu)
            ram = float(_metrics.mem)
            net = float(_metrics.net)
            temp = float(_metrics.tmp)
        except Exception:
            return

        net_pct = min(100.0, net * 8.0)
        temp_pct = 0 if temp < 0 else min(100.0, temp)

        def put(lbl_name, bar_name, text_value, pct):
            lbl = getattr(self, lbl_name, None)
            bar = getattr(self, bar_name, None)
            if lbl is not None:
                lbl.setText(text_value)
            if bar is not None:
                bar.setValue(max(0, min(100, int(pct))))

        put("_home_cpu", "_home_cpu_bar", f"{cpu:.0f}%", cpu)
        put("_home_ram", "_home_ram_bar", f"{ram:.0f}%", ram)
        put("_home_net", "_home_net_bar", f"{net:.2f} MB/s", net_pct)
        put("_home_temp", "_home_temp_bar", "N/A" if temp < 0 else f"{temp:.0f}°C", temp_pct)

        put("_sys_cpu", "_sys_cpu_bar", f"{cpu:.0f}%", cpu)
        put("_sys_ram", "_sys_ram_bar", f"{ram:.0f}%", ram)
        put("_sys_net", "_sys_net_bar", f"{net:.2f} MB/s", net_pct)
        put("_sys_temp", "_sys_temp_bar", "N/A" if temp < 0 else f"{temp:.0f}°C", temp_pct)

    def _mirror_kira_state(self, state: str) -> None:
        try:
            self._home_core.state = state
            self._home_core.update()
            self._home_state.setText("●  " + str(state).upper())
        except Exception:
            pass

    def _mirror_kira_content(self, title: str, body: str) -> None:
        txt = str(body or "").strip()
        if txt.lower().startswith("no news found"):
            title = "BRIEFING // STATUS"
            txt = (
                "No se encontraron noticias verificadas para esa consulta.\\n\\n"
                "KIRA sigue disponible.\\n"
                "• Prueba otra búsqueda.\\n"
                "• Revisa actividad reciente.\\n"
                "• El sistema y el contexto continúan activos."
            )

        try:
            self._home_briefing.setPlainText(
                str(title).upper() + "\\n\\n" + txt
            )
        except Exception:
            pass

'''
text = text[:insert_at] + methods + text[insert_at:]

# ------------------------------------------------------------------
# 3) Mirror existing live data into the new pages.
# ------------------------------------------------------------------
old = "        self._log_sig.connect(self._log.append_log)\n"
if old in text:
    text = text.replace(
        old,
        old
        + "        self._log_sig.connect(self._home_activity.append)\n"
        + "        self._log_sig.connect(self._tasks_feed.append)\n",
        1,
    )

old = "        self._state_sig.connect(self._apply_state)\n"
if old in text:
    text = text.replace(
        old,
        old + "        self._state_sig.connect(self._mirror_kira_state)\n",
        1,
    )

old = "        self._content_sig.connect(self._show_content)\n"
if old in text:
    text = text.replace(
        old,
        old + "        self._content_sig.connect(self._mirror_kira_content)\n",
        1,
    )

old = "        self._metric_tmr.timeout.connect(self._update_metrics)\n"
if old in text:
    text = text.replace(
        old,
        old + "        self._metric_tmr.timeout.connect(self._update_kira_page_metrics)\n",
        1,
    )

# Also make the original news/content panel useful when DDG returns nothing.
show_marker = "    def _show_content(self, title: str, text: str):\n"
show_pos = text.find(show_marker)
if show_pos != -1:
    body_pos = text.find("\n", show_pos + len(show_marker)) + 1
    fallback = (
        '        if str(text or "").strip().lower().startswith("no news found"):\n'
        '            title = "BRIEFING // STATUS"\n'
        '            text = ("No se encontraron noticias verificadas para esa consulta.\\n\\n"\n'
        '                    "KIRA está lista. Puedes intentar otra búsqueda o revisar HOME.")\n'
    )
    text = text[:body_pos] + fallback + text[body_pos:]

UI.write_text(text, encoding="utf-8")

# ------------------------------------------------------------------
# 4) Validate the resulting ui.py; rollback automatically on failure.
# ------------------------------------------------------------------
try:
    py_compile.compile(str(UI), doraise=True)
except Exception as e:
    print("❌ El ui.py resultante no compila:")
    print(e)
    shutil.copy2(backup, UI)
    print("↩️ Restaurado automáticamente desde ui.py.pre_kira_pages_v1")
    sys.exit(1)

print("")
print("✅ KIRA Real Pages v1 aplicado y validado")
print("• HOME ahora es un dashboard real")
print("• SYSTEM es una página separada")
print("• KIRA conserva el Orb v3 + briefing")
print("• TASKS ya es independiente")
print("• CHAT tiene su propia página")
print("• News vacío se convierte en briefing útil")
print("• Métricas y actividad se reflejan en HOME")
print("• Voz, Gemini, cámara, herramientas y acciones siguen intactos")
print("")
print("Ejecuta ahora:")
print("    python main.py")
print("")
print("Restaurar:")
print("    cp ui.py.pre_kira_pages_v1 ui.py")
