"""Integer-cent ledger with auditable, atomic planned-to-paid transitions."""
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from core.personal_store import PersonalStore

CATEGORIES = ('Universidad', 'Casa', 'Familia', 'Transporte', 'Comida', 'Servicios', 'Salud', 'Ocio', 'Ahorro', 'Otros')


def normalized(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(text).lower()) if not unicodedata.combining(c)).split())


def cents(value):
    text = normalized(value).replace('rd$', '').strip()
    multiplier = 1000 if text.endswith(' mil') else 1
    text = text.removesuffix(' mil').strip()
    # 29,000 / 29.000 are thousands; one or two final digits are decimals.
    if re.fullmatch(r'\d{1,3}(?:[,\.]\d{3})+(?:[,\.]\d{1,2})?', text):
        separators = [i for i,c in enumerate(text) if c in ',.']
        last = separators[-1]
        text = (text[:last].replace(',', '').replace('.', '') + '.' + text[last+1:]) if len(text)-last-1 <= 2 else text.replace(',', '').replace('.', '')
    else:
        text = text.replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', text):
        raise ValueError('Indica un monto positivo con hasta dos decimales')
    try:
        amount = Decimal(text) * multiplier * 100
        if amount != amount.to_integral_value() or not 0 < amount < 10**14:
            raise ValueError('Monto fuera de rango')
        return int(amount)
    except InvalidOperation as exc:
        raise ValueError('Monto no válido') from exc


def money(amount, currency='DOP'):
    return ('RD$' if currency == 'DOP' else currency + ' ') + f'{amount / 100:,.2f}'.removesuffix('.00')


def category_for(description):
    text = normalized(description)
    groups = {
        'Universidad': ('universidad', 'matricula'), 'Casa': ('alquiler', 'casa'),
        'Familia': ('mama', 'papa', 'familia'), 'Transporte': ('gasolina', 'transporte', 'taxi'),
        'Comida': ('comida', 'almuerzo', 'cena'), 'Servicios': ('internet', 'electricidad', 'servicios'),
        'Salud': ('medicina', 'salud', 'farmacia'), 'Ocio': ('cine', 'juego', 'ocio'),
        'Ahorro': ('ahorro', 'ahorrar'),
    }
    return next((name for name, words in groups.items() if any(w in text for w in words)), 'Otros')


class AmbiguousPayment(ValueError):
    def __init__(self, options):
        self.options = options
        super().__init__('Hay varios compromisos compatibles: ' + '; '.join(f"#{r['id']} {r['description']}" for r in options) + '. Indica «paga movimiento ID».')


class Wallet:
    def __init__(self, store=None, currency='DOP'):
        self.store = store or PersonalStore()
        if not re.fullmatch('[A-Z]{3}', currency):
            raise ValueError('Moneda no válida')
        self.currency = currency

    def record(self, kind, amount, description='', category=None):
        if kind not in ('income', 'planned', 'expense', 'reserve'):
            raise ValueError('Tipo de movimiento no válido')
        amount = cents(amount)
        category = category or category_for(description)
        if category not in CATEGORIES:
            raise ValueError('Categoría no válida')
        now = self.store.now()
        state = 'paid' if kind in ('income', 'expense') else 'open'
        with self.store.transaction() as db:
            cursor = db.execute('INSERT INTO movements(created,paid_at,kind,amount,currency,description,category,state) VALUES(?,?,?,?,?,?,?,?)',
                                (now, now if state == 'paid' else None, kind, amount, self.currency, description.strip(), category, state))
            mid = cursor.lastrowid
            db.execute('INSERT INTO wallet_events(movement_id,timestamp,action,detail) VALUES(?,?,?,?)', (mid, now, 'record', kind))
            return mid

    def pay(self, amount=None, description='', movement_id=None):
        value = cents(amount) if amount is not None else None
        now = self.store.now()
        with self.store.transaction() as db:
            if movement_id is not None:
                rows = db.execute("SELECT * FROM movements WHERE id=? AND currency=? AND id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='wallet')", (int(movement_id), self.currency)).fetchall()
                if not rows or rows[0]['kind'] not in ('planned','reserve'):
                    raise ValueError('No existe ese compromiso')
                row = rows[0]
                if value is not None and value != row['amount']:
                    raise ValueError('El monto contradice el compromiso')
                if row['state'] == 'paid':
                    return row['id'], False
            else:
                if value is None:
                    raise ValueError('Falta el monto')
                rows = db.execute("SELECT * FROM movements WHERE amount=? AND currency=? AND state='open' AND kind IN ('planned','reserve') AND id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='wallet')", (value, self.currency)).fetchall()
                rows = [r for r in rows if normalized(r['description']) == normalized(description)]
                if len(rows) > 1:
                    raise AmbiguousPayment(rows)
                row = rows[0] if rows else None
            if row:
                db.execute("UPDATE movements SET state='paid',paid_at=? WHERE id=?", (now, row['id']))
                mid = row['id']
                action = 'paid'
            else:
                mid = db.execute("INSERT INTO movements(created,paid_at,kind,amount,currency,description,category,state) VALUES(?,?,'expense',?,?,?,?,'paid')", (now, now, value, self.currency, description, category_for(description))).lastrowid
                action = 'record'
            db.execute('INSERT INTO wallet_events(movement_id,timestamp,action,detail) VALUES(?,?,?,?)', (mid, now, action, 'expense'))
            return mid, True

    def recategorize(self, movement_id, category):
        category = next((c for c in CATEGORIES if normalized(c) == normalized(category)), None)
        if category is None:
            raise ValueError('Categoría no válida')
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM movements WHERE id=? AND currency=?', (int(movement_id), self.currency)).fetchone()
            if row is None:
                raise ValueError('Movimiento no encontrado')
            db.execute('UPDATE movements SET category=? WHERE id=?', (category, row['id']))
            db.execute('INSERT INTO wallet_events(movement_id,timestamp,action,detail) VALUES(?,?,?,?)', (row['id'], self.store.now(), 'category', category))

    def movements(self, pending=False):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM movements WHERE currency=? AND id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='wallet')" + (" AND state='open'" if pending else '') + ' ORDER BY id DESC', (self.currency,))]

    def summary(self, period='month'):
        if period not in ('today','month'):
            raise ValueError('Periodo no válido')
        now = self.store.now()
        prefix = now[:10] if period == 'today' else now[:7]
        totals = dict(income=0, planned=0, reserve=0, spent=0)
        categories = {}
        for r in self.movements():
            if r['state'] == 'open':
                totals[r['kind']] += r['amount']
            elif (r['paid_at'] or r['created']).startswith(prefix):
                key = 'income' if r['kind'] == 'income' else 'spent'
                totals[key] += r['amount']
                if key == 'spent':
                    categories[r['category']] = categories.get(r['category'], 0) + r['amount']
        totals['available'] = totals['income'] - totals['spent'] - totals['planned'] - totals['reserve']
        totals['categories'] = categories
        return totals

    def summary_text(self, period='month'):
        s = self.summary(period)
        return '\n'.join(f'{name}: {money(s[key], self.currency)}' for name,key in (
            ('Ingresos','income'), ('Planeado','planned'), ('Apartado / ahorro','reserve'), ('Gastado real','spent'), ('Disponible estimado','available')))
