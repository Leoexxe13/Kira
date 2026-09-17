"""Local work/wallet pages. Documents keep their existing widget and callbacks."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QLineEdit, QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog, QMenu)
from core.personal_store import PersonalStore
from core.personal_commands import handle
from core.wallet import Wallet, CATEGORIES, money
from core.work import Work
from ui_hud import Panel, label


def table(headers):
    widget = QTableWidget(0,len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    widget.verticalHeader().hide()
    return widget


def rows(widget, values):
    widget.blockSignals(True)
    widget.setRowCount(len(values))
    for i, row in enumerate(values):
        for j, value in enumerate(row):widget.setItem(i,j,QTableWidgetItem(str(value)))
    widget.blockSignals(False)


class PersonalPage(QWidget):
    request_done = pyqtSignal(object)
    def __init__(self, domain, store=None, logger=None):
        super().__init__()
        self.domain = domain
        self.store = store or PersonalStore()
        self.logger = logger or (lambda _:None)
        from core.dispatcher import shared_dispatcher
        self.dispatcher = shared_dispatcher(self.store)
        self.request_done.connect(self._request_done)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,8,8,8)
        self.status = label('',10,True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.cards = label('',12)
        layout.addWidget(self.cards)
        commands = QHBoxLayout()
        if domain == 'work':
            for title, command in (('Iniciar turno','Empieza mi turno'),('Cerrar y resumir','Cierra el turno y hazme un resumen'),('Resumen de hoy','Qué pasó hoy')):
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False,c=command:self.execute(c))
                commands.addWidget(button)
            self.views = QTabWidget()
            self.history = table(('ID','Hora','Categoría','Descripción','Persona','Recurso'))
            self.resources = table(('Recurso','Persona actual','Estado','Hora','Evento'))
            self.pending = table(('ID','Tipo','Descripción','Hora'))
            self.views.addTab(self.history,'BITÁCORA')
            self.views.addTab(self.resources,'VEHÍCULOS / FICHAS')
            self.views.addTab(self.pending,'INCIDENCIAS / PENDIENTES')
            finish = QPushButton('Completar pendiente seleccionado')
            finish.clicked.connect(self.complete_pending)
            commands.addWidget(finish)
            layout.addLayout(commands)
            layout.addWidget(self.views,1)
            example = 'Ej.: Le entregué la ficha 17 a Carlos · Registra incidencia: neumático averiado'
        else:
            self.period = QComboBox()
            self.period.addItem('Este mes','month'); self.period.addItem('Hoy','today')
            self.period.currentIndexChanged.connect(self.refresh)
            commands.addWidget(self.period)
            pay = QPushButton('Marcar pagado el compromiso seleccionado')
            pay.clicked.connect(self.pay_selected)
            commands.addWidget(pay)
            self.category = QComboBox(); self.category.addItems(CATEGORIES)
            commands.addWidget(self.category)
            change = QPushButton('Cambiar categoría')
            change.clicked.connect(self.change_category)
            commands.addWidget(change)
            layout.addLayout(commands)
            layout.addWidget(label('Disponible estimado = ingresos del periodo − gastos reales del periodo − compromisos abiertos − apartados abiertos. Sin saldo bancario ni arrastre de meses anteriores.',9,True))
            self.history = table(('ID','Fecha','Concepto','Categoría','Monto','Tipo','Estado'))
            layout.addWidget(self.history,1)
            example = 'Ej.: Cobré 29 mil · Voy a pagar 5,000 de universidad · Aparta 2,000 para ahorrar'
        actions = QHBoxLayout()
        for title, callback in (('Detalles', self.details), ('Editar', self.edit_selected), ('Eliminar', self.delete_selected), ('Deshacer', lambda: self.execute('Deshaz eso'))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            actions.addWidget(button)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar en los registros…')
        self.search.textChanged.connect(self.filter_rows)
        actions.addWidget(self.search, 1)
        layout.addLayout(actions)
        self.history.itemSelectionChanged.connect(self.publish_selection)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(label(example,9,True))
        entry = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText('Escribe una orden local…')
        self.command.returnPressed.connect(self.submit)
        entry.addWidget(self.command,1)
        submit = QPushButton('Registrar / consultar')
        submit.clicked.connect(self.submit); entry.addWidget(submit)
        layout.addLayout(entry)
        layout.addWidget(self.status)

    def selected(self, widget):
        index = widget.currentRow()
        return widget.item(index,0).text() if index >= 0 else None

    def complete_pending(self):
        identity = self.selected(self.pending)
        if identity:self.execute('Completa pendiente de trabajo ' + identity)

    def pay_selected(self):
        identity = self.selected(self.history)
        if identity:self.execute('Paga movimiento ' + identity)

    def change_category(self):
        identity = self.selected(self.history)
        if identity:self.execute(f'Cambia categoría movimiento {identity} a {self.category.currentText()}')

    def submit(self):
        text=self.command.text().strip()
        if not text:return
        self.publish_selection()
        self.command.clear()
        self.status.setText('Procesando…')
        import threading
        threading.Thread(target=lambda:self.request_done.emit(self.dispatcher.dispatch_user_request(text,source='hub')),daemon=True).start()

    def _request_done(self, result):
        self.status.setText(result.text)
        self.refresh()

    def execute(self, command):
        self.publish_selection()
        result = self.dispatcher.dispatch_user_request(command, source='hub')
        self.status.setText(result.text)
        if result.handled:
            self.logger('SYS: Registro personal ' + result.state)
            self.refresh()
        return result.handled and result.state=='verified'

    def publish_selection(self):
        from core.intent_context import publish_ui
        selected = [int(self.history.item(i.row(),0).text()) for i in self.history.selectionModel().selectedRows() if self.history.item(i.row(),0)]
        visible = [int(self.history.item(i,0).text()) for i in range(self.history.rowCount()) if not self.history.isRowHidden(i) and self.history.item(i,0)]
        publish_ui(self.store, self.domain, selected=selected, visible=visible, filter=getattr(self,'search',None).text() if hasattr(self,'search') else '')

    def filter_rows(self, *_):
        query = self.search.text().casefold()
        for row in range(self.history.rowCount()):
            self.history.setRowHidden(row, query not in ' '.join(self.history.item(row,c).text() for c in range(self.history.columnCount())).casefold())
        self.publish_selection()

    def details(self):
        row = self.history.currentRow()
        if row >= 0:
            QMessageBox.information(self, 'Detalles del registro', '\n'.join(self.history.horizontalHeaderItem(c).text()+': '+self.history.item(row,c).text() for c in range(self.history.columnCount())))

    def edit_selected(self):
        if self.history.currentRow() < 0: return
        text, ok = QInputDialog.getText(self, 'Editar registro', 'Indica el cambio (por ejemplo: eran 1800 o era Pedro):')
        if ok and text.strip(): self.execute(text)

    def delete_selected(self):
        if self.history.currentRow() < 0: return
        self.execute('Elimina el registro seleccionado')
        if QMessageBox.question(self, 'Confirmar eliminación', self.status.text()) == QMessageBox.StandardButton.Yes:
            self.execute('Sí')
        else:
            self.execute('Cancelar')

    def context_menu(self, position):
        menu = QMenu(self)
        for title, callback in (('Detalles',self.details), ('Editar',self.edit_selected), ('Eliminar',self.delete_selected)):
            menu.addAction(title, callback)
        menu.exec(self.history.viewport().mapToGlobal(position))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, *_):
        try:
            from core.intent_context import publish_ui, context_for
            if self.domain == 'work':
                work = Work(self.store)
                shift = work.active_shift()
                self.cards.setText(f"TURNO #{shift['id']} · desde {shift['started']}" if shift else 'SIN TURNO ACTIVO')
                rows(self.history,[(r['id'],r['timestamp'],r['category'],r['description'],r['person'],r['resource']) for r in work.events()[:200]])
                rows(self.resources,[(r['resource'],r['person'] or '—',{'available':'Devuelto','assigned':'Asignado','out':'Fuera'}[r['state']],r['timestamp'],r['event_id']) for r in work.resources()])
                rows(self.pending,[(r['id'],r['category'],r['description'],r['timestamp']) for r in work.pending()])
                if self.isVisible(): publish_ui(self.store, 'work',
                           selected=[self.pending.item(self.pending.currentRow(),0).text()] if self.pending.currentRow() >= 0 else [],
                           visible=[r['id'] for r in work.events()[:200]], filter=self.views.tabText(self.views.currentIndex()))
            else:
                wallet = Wallet(self.store)
                self.cards.setText(wallet.summary_text(self.period.currentData()).replace('\n','   |   '))
                types = {'income':'Ingreso','planned':'Planeado','expense':'Gasto real','reserve':'Apartado'}
                rows(self.history,[(r['id'],r['created'],r['description'],r['category'],money(r['amount'],r['currency']),types[r['kind']], 'Pagado' if r['state']=='paid' else 'Pendiente') for r in wallet.movements()[:200]])
                if self.isVisible(): publish_ui(self.store, 'wallet',
                           selected=[self.history.item(self.history.currentRow(),0).text()] if self.history.currentRow() >= 0 else [],
                           visible=[r['id'] for r in wallet.movements()[:200]], filter=self.period.currentData())
            if hasattr(self,'search') and self.isVisible(): self.filter_rows()
        except Exception as exc:
            self.status.setText('No pude leer el registro local: ' + type(exc).__name__)


class PersonalHub(QTabWidget):
    def __init__(self, documents, store=None, logger=None):
        super().__init__()
        self.addTab(documents,'DOCUMENTOS')
        self.work_page = PersonalPage('work',store,logger)
        self.wallet_page = PersonalPage('wallet',store,logger)
        self.addTab(self.work_page,'TRABAJO')
        self.addTab(self.wallet_page,'BILLETERA')

    def refresh_visible(self):
        page = self.currentWidget()
        if isinstance(page,PersonalPage) and page.isVisible():page.refresh()
