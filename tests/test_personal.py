"""Synthetic local SQLite records. No network, real balances or user files."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.personal_store import PersonalStore
from core.wallet import Wallet, AmbiguousPayment, cents
from core.work import Work
from core.personal_commands import handle
from core.intent_interpreter import run


class PersonalFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'personal.db'
        self.clock = lambda:datetime(2026,9,15,12,tzinfo=timezone.utc)
        self.store = PersonalStore(self.path,self.clock)


class WalletTests(PersonalFixture):
    def setUp(self):
        super().setUp()
        self.wallet = Wallet(self.store)

    def test_income(self):
        self.wallet.record('income','29 mil')
        self.assertEqual(self.wallet.summary()['income'],2900000)

    def test_natural_language_money_variants(self):
        for phrase in ('Pagué 1500 de universidad', 'Pagué mil quinientos de universidad',
                       'Le di 1500 a la universidad', 'Gasté 1500 en la uni'):
            with self.subTest(phrase=phrase):
                result = run(phrase, self.store)
                self.assertTrue(result.handled)
                self.assertEqual(result.domain, 'wallet')
                self.assertEqual(result.intent, 'MARK_PAID')
        self.assertEqual(len(self.wallet.movements()), 4)

    def test_natural_income_and_planned_variants(self):
        self.assertEqual(run('Cobré once mil', self.store).intent, 'CREATE')
        self.assertEqual(run('Me pagaron 11000 pesos', self.store).intent, 'CREATE')
        self.assertEqual(run('Voy a pagar 5000 de universidad', self.store).intent, 'CREATE')
        self.assertEqual(run('Ya pagué lo de universidad', self.store).state, 'verified')

    def test_approximate_money_requests_exact_value(self):
        result = run('Gasté como dos mil y algo en comida', self.store)
        self.assertEqual(result.state, 'pending')
        self.assertIn('exactamente', result.text)

    def test_planned_is_not_spent(self):
        self.wallet.record('planned','5,000','universidad')
        s = self.wallet.summary()
        self.assertEqual((s['planned'],s['spent']),(500000,0))

    def test_real_expense(self):
        self.wallet.record('expense','600','gasolina')
        s = self.wallet.summary()
        self.assertEqual(s['spent'],60000)
        self.assertEqual(s['categories'],{'Transporte':60000})

    def test_reserve_separate(self):
        self.wallet.record('reserve','3,000','mi mamá')
        s = self.wallet.summary()
        self.assertEqual((s['reserve'],s['spent']),(300000,0))

    def test_available_example(self):
        for command in ('Cobré 29,000','Voy a pagar 5,000 de universidad','Aparta 3,000 para mi mamá','Gasté 600 de gasolina'):
            self.assertEqual(handle(command,self.store).state,'verified')
        self.assertEqual(self.wallet.summary()['available'],2040000)

    def test_planned_paid_no_double_count(self):
        self.wallet.record('income','29,000')
        mid = self.wallet.record('planned','5000','universidad')
        before = self.wallet.summary()['available']
        result = handle('Ya pagué los 5,000 de universidad',self.store)
        self.assertEqual(result.state,'verified')
        self.assertEqual(self.wallet.summary()['available'],before)
        self.assertEqual(len(self.wallet.movements()),2)
        self.assertEqual(self.wallet.pay(movement_id=mid),(mid,False))
        with self.store.transaction() as db:
            events = db.execute('SELECT action FROM wallet_events WHERE movement_id=? ORDER BY id',(mid,)).fetchall()
        self.assertEqual([r['action'] for r in events],['record','paid'])

    def test_reserve_to_paid(self):
        mid = self.wallet.record('reserve','2000','familia')
        self.wallet.pay('2000','familia')
        s = self.wallet.summary()
        self.assertEqual((s['reserve'],s['spent']),(0,200000))
        self.assertEqual(len(self.wallet.movements()),1)

    def test_ambiguous_payment_no_mutation(self):
        for _ in range(2):self.wallet.record('planned','5000','universidad')
        with self.assertRaises(AmbiguousPayment):self.wallet.pay('5000','universidad')
        self.assertEqual(self.wallet.summary()['planned'],1000000)
        self.assertEqual(self.wallet.summary()['spent'],0)

    def test_wrong_amount_does_not_pay(self):
        mid = self.wallet.record('planned','5000','universidad')
        with self.assertRaises(ValueError):self.wallet.pay('6000',movement_id=mid)
        self.assertEqual(self.wallet.movements()[0]['state'],'open')

    def test_persistence(self):
        self.wallet.record('expense','450','comida')
        self.assertEqual(Wallet(PersonalStore(self.path,self.clock)).summary()['spent'],45000)

    def test_offline_commands_do_not_call_provider(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('network')), patch('requests.post',side_effect=AssertionError('network')):
            for command in ('Me pagaron 29,000','Gasté 450 en comida','Pagué 1,200 de gasolina','Voy a gastar 5,000 de universidad','Aparta 2,000 para ahorrar','¿Cuánto me queda?','¿Cuánto tengo comprometido?','¿En qué he gastado más este mes?','¿Qué gastos me faltan por pagar?','Muéstrame mis últimos gastos'):
                result = handle(command,self.store)
                self.assertTrue(result.handled,command)
                self.assertEqual(result.state,'verified',result.text)

    def test_ambiguous_dale_does_not_record(self):
        self.assertEqual(handle('Dale 3,000 a familia',self.store).state,'pending')
        self.assertFalse(self.path.exists())

    def test_categories_can_change(self):
        mid = self.wallet.record('expense','100','otro concepto')
        self.wallet.recategorize(mid,'Salud')
        self.assertEqual(self.wallet.movements()[0]['category'],'Salud')

    def test_currencies_do_not_mix(self):
        self.wallet.record('income','100')
        Wallet(self.store,'USD').record('income','200')
        self.assertEqual(self.wallet.summary()['income'],10000)

    def test_cent_precision(self):
        for text, expected in [('1,200',120000),('1.200',120000),('29 mil',2900000),('12.50',1250),('1,200.50',120050),('1.200,50',120050)]:
            self.assertEqual(cents(text),expected,text)
        for invalid in ('-5','0','NaN','1.3333','abc'):
            with self.assertRaises(ValueError):cents(invalid)

    def test_period_payment_uses_payment_date(self):
        past = PersonalStore(self.path,lambda:datetime(2026,8,1,tzinfo=timezone.utc))
        mid = Wallet(past).record('planned','100','universidad')
        self.wallet.pay(movement_id=mid)
        self.assertEqual(self.wallet.summary('today')['spent'],10000)
        self.assertEqual(self.wallet.summary('month')['planned'],0)


class WorkTests(PersonalFixture):
    def setUp(self):
        super().setUp()
        self.work = Work(self.store)

    def test_start_idempotent(self):
        sid,created = self.work.start_shift()
        self.assertTrue(created)
        self.assertEqual(self.work.start_shift(),(sid,False))

    def test_natural_assignment_and_correction(self):
        self.assertEqual(run('Le di la 75 a Carlos', self.store).intent, 'ASSIGN')
        result = run('La ficha no era la 75, era la 76', self.store)
        self.assertEqual(result.state, 'verified')
        self.assertEqual(self.work.holder('76')['person'], 'Carlos')

    def test_natural_delete_requires_confirmation_and_undo(self):
        self.work.event('note', 'Prueba de trabajo')
        result = run('Borra el último evento', self.store)
        self.assertEqual(result.state, 'pending')
        self.assertIn('Elimino', result.text)
        self.assertEqual(run('Sí', self.store).state, 'verified')
        self.assertEqual(len(self.work.events()), 0)
        self.assertEqual(run('Deshaz eso', self.store).state, 'verified')
        self.assertEqual(len(self.work.events()), 1)

    def test_context_updates_previous_wallet_movement(self):
        wallet = Wallet(self.store)
        self.assertEqual(run('Gasté 1200 en transporte', self.store).state, 'verified')
        self.assertEqual(run('Eso fue comida, no transporte', self.store).state, 'verified')
        row = wallet.movements()[0]
        self.assertEqual(row['category'], 'Comida')
        self.assertEqual(run('Cambia ese gasto a 1800', self.store).state, 'verified')
        self.assertEqual(wallet.movements()[0]['amount'], 180000)

    def test_bulk_today_requires_exact_confirmed_set(self):
        self.work.event('note', 'Prueba uno')
        self.work.event('note', 'Prueba dos')
        result = run('Borra los eventos de prueba', self.store)
        self.assertEqual(result.state, 'pending')
        self.assertIn('2 registro', result.text)
        self.assertEqual(run('Sí', self.store).state, 'verified')
        self.assertEqual(self.work.events(), [])

    def test_log_event(self):
        self.work.event('note','Control completado',source='manual',notes='sintético')
        row = self.work.events()[0]
        self.assertEqual(row['source'],'manual')
        self.assertEqual(row['notes'],'sintético')

    def test_assign_query_return_history(self):
        self.work.start_shift()
        assign = self.work.event('assign','Entrega',resource='17',person='Carlos')
        self.assertEqual(self.work.holder('ficha 17')['person'],'Carlos')
        self.assertEqual(self.work.holder('17')['event_id'],assign)
        self.work.event('return','Devuelta',resource='17')
        self.assertEqual(self.work.holder('17')['state'],'available')
        self.assertEqual(len(self.work.events()),2)

    def test_correction_preserves_resource_and_history(self):
        self.work.event('assign','Entrega',resource='F-37',person='Carlos')
        result = handle('Corrige la ficha F37 la tiene Pérez',self.store)
        self.assertEqual(result.state,'verified')
        self.assertEqual(len(self.work.resources()),1)
        self.assertEqual(len(self.work.events()),2)
        self.assertEqual(self.work.holder('F-37')['person'],'Pérez')

    def test_departure_keeps_reported_time(self):
        result = handle('KIRA, registra que la F-37 salió a las 8:20 con Pérez.',self.store)
        self.assertEqual(result.state,'verified',result.text)
        self.assertEqual(self.work.holder('F37')['timestamp'][11:16],'08:20')

    def test_invalid_time_rolls_back(self):
        result = handle('Registra que la F-37 salió a las 99:20 con Pérez',self.store)
        self.assertEqual(result.state,'failed')
        self.assertFalse(self.path.exists())

    def test_incidents_and_pending(self):
        self.work.event('incident','Neumático averiado')
        eid = self.work.event('pending','Revisar llaves')
        self.assertEqual(len(self.work.pending()),2)
        self.work.complete(eid)
        self.assertEqual(len(self.work.pending()),1)
        self.assertEqual(len(self.work.events()),3)

    def test_close_summary_retains_open_pending(self):
        self.work.start_shift()
        self.work.event('pending','Revisar vehículo')
        summary = self.work.close_shift()
        self.assertIn('Revisar vehículo',summary)
        self.assertIsNone(self.work.active_shift())
        self.work.start_shift()
        self.assertEqual(len(self.work.pending()),1)

    def test_persistence(self):
        self.work.event('assign','Entrega',resource='17',person='Carlos')
        self.assertEqual(Work(PersonalStore(self.path,self.clock)).holder('17')['person'],'Carlos')

    def test_offline_work_commands(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('network')):
            for command in ('Activa modo trabajo','Le entregué la ficha 17 a Carlos','Quién tiene la ficha 17','La ficha 17 volvió','Registra incidencia: retraso','Crea pendiente de trabajo: revisar','Qué incidencias tengo','Qué pendientes quedan','Qué pasó hoy','Cierra el turno y hazme un resumen'):
                result = handle(command,self.store)
                self.assertTrue(result.handled,command)
                self.assertEqual(result.state,'verified',result.text)

    def test_no_unknown_return_or_invented_holder(self):
        self.assertIsNone(self.work.holder('17'))
        with self.assertRaises(ValueError):self.work.event('return','Devuelta',resource='17')
        self.assertEqual(self.work.events(),[])

    def test_private_fast_path_does_not_leave_local_route(self):
        from core.local_fastpath import handle as fast
        with patch('core.personal_store.DEFAULT_PATH',self.path):
            result = fast('Cobré 29 mil')
        self.assertTrue(result.handled)
        self.assertTrue(result.private)


if __name__=='__main__':unittest.main()
