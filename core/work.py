"""Work event history and current resource state, updated in one transaction."""
from datetime import datetime
import re
from core.personal_store import PersonalStore


def resource_key(value):
    value = re.sub(r'^(?:la\s+)?(?:ficha|vehículo|vehiculo)\s+', '', str(value).strip(), flags=re.I)
    key = re.sub(r'[\s-]+', '', value).upper()
    if not re.fullmatch(r'[A-Z0-9]{1,30}', key):
        raise ValueError('Identificador de recurso no válido')
    return key


class Work:
    def __init__(self, store=None):
        self.store = store or PersonalStore()

    def active_shift(self):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM shifts WHERE ended IS NULL').fetchone()
            return dict(row) if row else None

    def start_shift(self):
        with self.store.transaction() as db:
            row = db.execute('SELECT id FROM shifts WHERE ended IS NULL').fetchone()
            if row:
                return row['id'], False
            sid = db.execute('INSERT INTO shifts(started) VALUES(?)', (self.store.now(),)).lastrowid
            return sid, True

    def event(self, category, description, person='', resource='', state='recorded', hour=None, source='local', notes=''):
        if category not in ('note','assign','return','departure','incident','pending','correction'):
            raise ValueError('Categoría laboral no válida')
        now = self.store.now()
        stamp = now
        if hour:
            h,m = map(int, hour.split(':'))
            stamp = datetime.fromisoformat(now).replace(hour=h, minute=m, second=0).isoformat(timespec='seconds')
        resource = resource_key(resource) if resource else ''
        if category in ('assign','departure','correction') and (not resource or not person.strip()):
            raise ValueError('Indica el recurso y la persona')
        if category == 'return' and not resource:
            raise ValueError('Indica el recurso que volvió')
        with self.store.transaction() as db:
            shift = db.execute('SELECT id FROM shifts WHERE ended IS NULL').fetchone()
            sid = shift['id'] if shift else None
            if category == 'return' and not db.execute('SELECT 1 FROM resources WHERE resource=?', (resource,)).fetchone():
                raise ValueError('No hay registro previo de ese recurso')
            eid = db.execute('INSERT INTO work_events(timestamp,recorded_at,shift_id,category,description,person,resource,state,source,notes) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (stamp,now,sid,category,description,person,resource,state,source,notes)).lastrowid
            if category in ('assign','departure','return','correction'):
                current = 'available' if category == 'return' else ('out' if category == 'departure' else 'assigned')
                db.execute('INSERT INTO resources(resource,person,state,event_id,timestamp) VALUES(?,?,?,?,?) ON CONFLICT(resource) DO UPDATE SET person=excluded.person,state=excluded.state,event_id=excluded.event_id,timestamp=excluded.timestamp',
                           (resource,'' if category == 'return' else person,current,eid,stamp))
            if category in ('pending','incident'):
                db.execute('INSERT INTO work_pending(event_id) VALUES(?)', (eid,))
            return eid

    def holder(self, resource):
        with self.store.transaction() as db:
            key = resource_key(resource)
            row = db.execute('SELECT * FROM resources WHERE resource=?', (key,)).fetchone()
            if row: return dict(row)
            # Repair/read compatibility for databases created before the
            # resources projection was rebuilt: derive the current state from
            # the surviving event history instead of claiming it is unknown.
            events = db.execute("SELECT * FROM work_events WHERE resource=? AND category IN ('assign','departure','return','correction') AND id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='work') ORDER BY id", (key,)).fetchall()
            if not events: return None
            event = events[-1]
            if event['category'] == 'return':
                return {'resource':key,'person':'','state':'available','event_id':event['id'],'timestamp':event['timestamp']}
            return {'resource':key,'person':event['person'],'state':'out' if event['category']=='departure' else 'assigned','event_id':event['id'],'timestamp':event['timestamp']}

    def describe_holder(self, resource):
        row = self.holder(resource)
        if row:
            text = f"La tiene {row['person']}" if row['person'] else 'La ficha está devuelta'
            return row, f"{text} · {row['timestamp']}"
        with self.store.transaction() as db:
            removed = db.execute("SELECT 1 FROM work_events WHERE resource=? AND category IN ('assign','departure','return','correction') AND id IN (SELECT item_id FROM personal_hidden WHERE domain='work') LIMIT 1", (resource_key(resource),)).fetchone()
        text = (f'Los registros de la ficha {resource_key(resource)} fueron eliminados; no hay un tenedor vigente.'
                if removed else 'No hay un tenedor actual registrado.')
        return None, text

    def resources(self):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM resources ORDER BY resource')]

    def events(self, shift_id=None, today=False):
        where, args = " WHERE id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='work')", []
        if shift_id is not None:
            where, args = where+' AND shift_id=?', [shift_id]
        elif today:
            where, args = where+' AND substr(timestamp,1,10)=?', [self.store.now()[:10]]
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM work_events' + where + ' ORDER BY id DESC', args)]

    def pending(self, category=None):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute("SELECT e.* FROM work_pending p JOIN work_events e ON e.id=p.event_id WHERE p.state='open' AND e.id NOT IN (SELECT item_id FROM personal_hidden WHERE domain='work')" + (' AND e.category=?' if category else '') + ' ORDER BY e.id', (category,) if category else ())]

    def complete(self, event_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT 1 FROM work_pending WHERE event_id=? AND state='open'", (int(event_id),)).fetchone()
            if not row:
                raise ValueError('No existe ese pendiente abierto')
            db.execute("UPDATE work_pending SET state='completed' WHERE event_id=?", (int(event_id),))
            now = self.store.now()
            db.execute("INSERT INTO work_events(timestamp,recorded_at,category,description,state,related_id) VALUES(?,?,'note','Pendiente completado','completed',?)", (now,now,int(event_id)))

    def close_shift(self):
        with self.store.transaction() as db:
            shift = db.execute('SELECT id FROM shifts WHERE ended IS NULL').fetchone()
            if shift is None:
                raise ValueError('No hay un turno activo')
            db.execute('UPDATE shifts SET ended=? WHERE id=?', (self.store.now(),shift['id']))
        return self.summary(shift['id'])

    def summary(self, shift_id=None):
        events = self.events(shift_id=shift_id, today=shift_id is None)
        lines = [f"{len(events)} eventos registrados. {len(self.pending())} pendientes abiertos."]
        lines += [f"#{e['id']} · {e['timestamp'][11:16]} · {e['description']}" for e in reversed(events[:20])]
        return '\n'.join(lines)
