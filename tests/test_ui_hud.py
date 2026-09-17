"""UI tests use temporary attachments and task fixtures; no live tool execution."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QPushButton, QLabel
from ui import MainWindow
from ui_hud import Sparkline


class HudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        for name, value in (('_check_config', True), ('_load_kira_tasks', []), ('_sync_tasks_from_disk', None), ('_refresh_provider_status_v4', None)):
            p = patch.object(MainWindow, name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        self.window = MainWindow('missing-test-face.png')
        self.addCleanup(self.window.close)
        self.window._metric_tmr.stop()
        self.window.show()
        self.app.processEvents()

    def test_core_dominates_at_window_sizes(self):
        for width, height in ((980,700), (1280,800), (1440,900)):
            self.window.resize(width,height)
            self.window._switch_workspace('home')
            self.app.processEvents()
            self.assertGreater(self.window._home_core.height(), self.window._home_briefing.height())
            self.assertGreater(self.window._home_command.width(), 180)
            self.assertTrue(self.window._home_command.isVisible())

    def test_settings_resize_keeps_existing_controls(self):
        self.window._switch_workspace('settings')
        for width in (980,1280):
            self.window.resize(width,800); self.app.processEvents()
            self.assertGreater(self.window._quick_drawer.width(), 800)
            self.assertTrue(self.window._autostart_btn.isVisible())
            self.assertTrue(self.window._wake_btn.isVisible())
        self.assertEqual(len([b for b in self.window._quick_drawer.findChildren(QPushButton) if b is self.window._autostart_btn]),1)

    def test_attachment_selection_clear_and_draft_share_backend_context(self):
        with tempfile.TemporaryDirectory() as d:
            first = Path(d)/'one.txt'; first.write_text('Documento de prueba')
            second = Path(d)/'two.txt'; second.write_text('Otro documento')
            callback = Mock()
            self.window.on_text_command = callback
            self.window._on_file_selected(str(first))
            self.assertIn('Documento de prueba', self.window._knowledge_recent.toPlainText())
            self.window._on_file_selected(str(second))
            self.assertEqual(self.window._context_files.count(),2)
            self.window._select_context_item(self.window._context_files.item(1))
            self.assertEqual(self.window._drop_zone.current_file(),str(first))
            self.window._knowledge_summarize()
            self.assertIn('one.txt',self.window._input.text())
            callback.assert_not_called()
            self.window._knowledge_clear()
            self.assertIsNone(self.window._drop_zone.current_file())
            self.assertFalse(any(b.isEnabled() for b in self.window._context_actions))

    def test_catalog_uses_registered_tools_and_search(self):
        self.window.get_tools = lambda: [dict(name='whatsapp_web',description='Contactos'),dict(name='file_processor',description='Archivos')]
        self.window._refresh_tools_page()
        self.assertEqual(len(self.window._tools_cards),2)
        self.window._nav_search.setText('whatsapp')
        self.app.processEvents()
        self.assertEqual(sum(card.isVisible() for _,card in self.window._tools_cards),1)
        self.assertIn('REGISTRADAS', self.window._tools_count.text())

    def test_provider_panel_never_exposes_markup(self):
        self.window._home_provider_status.update_providers([
            ('', '#88aaff', 'Gemini', 'modelo de prueba', 'Voz · visión', 'CONECTADO'),
            ('', '#eeeeee', 'Local', 'modelo local', 'Texto', 'DISPONIBLE'),
        ], 'OFFLINE')
        texts = [label.text() for label in self.window._home_provider_status.findChildren(QLabel)]
        visible = '\n'.join(texts)
        self.assertIn('Gemini', visible)
        self.assertIn('modelo de prueba', visible)
        for tag in ('<span>', '</span>', '<b>', '</b>', '<small>', '</small>', '<br>'):
            self.assertNotIn(tag, visible)

    def test_personal_pages_initialize_and_persist_offscreen(self):
        from core.personal_store import PersonalStore
        from ui_personal import PersonalPage
        with tempfile.TemporaryDirectory() as directory:
            store = PersonalStore(Path(directory)/'personal.db')
            for domain, command in [('work','Empieza mi turno'),('wallet','Cobré 29 mil')]:
                page = PersonalPage(domain, store=store)
                page.show(); self.app.processEvents()
                page.execute(command)
                self.assertTrue(page.status.text())
                page.close()

    def test_no_invented_task_status(self):
        self.assertEqual(MainWindow._task_bucket({'done':False}), 'PENDIENTES')
        self.assertEqual(MainWindow._task_bucket({'done':True}), 'COMPLETADAS')
        self.assertEqual(MainWindow._task_bucket({'status':'running'}), 'EN CURSO')
        self.assertEqual(MainWindow._task_bucket({'status':'cancelled'}), 'CANCELADAS')

    def test_history_contains_only_samples_and_is_bounded(self):
        chart = Sparkline()
        self.assertEqual(len(chart.values),0)
        chart.sample(None); chart.sample(float('nan'))
        self.assertEqual(len(chart.values),0)
        for value in range(100): chart.sample(value)
        self.assertEqual(len(chart.values),60)
        self.assertEqual(chart.values[-1],99)

    def test_hidden_terrain_stops_animation(self):
        self.assertTrue(self.window._terrain.timer.isActive())
        self.window._switch_workspace('chat'); self.app.processEvents()
        self.assertFalse(self.window._terrain.timer.isActive())

if __name__ == '__main__': unittest.main()
