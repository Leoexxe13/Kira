"""Transactional reversible CRUD for personal records. Original rows retained.

Work projections are rebuilt after revisions, so deleting an assignment cannot
leave a fictitious current holder. Optimistic snapshots reject stale confirmations.
"""
import json
from core.work import resource_key
from core.wallet import CATEGORIES, cents

def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS personal_changes (id INTEGER PRIMARY KEY, domain TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS personal_hidden (domain TEXT NOT NULL, item_id INTEGER NOT NULL, PRIMARY KEY(domain,item_id))')

def visible(db,domain,rows):
    hidden={r[0] for r in db.execute('SELECT item_id FROM personal_hidden WHERE domain=?',(domain,))}
    return [dict(r) for r in rows if r['id'] not in hidden]

def rebuild(db):
    db.execute('DELETE FROM resources')
    for row in visible(db,'work',db.execute("SELECT * FROM work_events WHERE category IN ('assign','departure','return','correction') ORDER BY id")):
        if not row['resource']:continue
        state='available' if row['category']=='return' else ('out' if row['category']=='departure' else 'assigned')
        db.execute('INSERT INTO resources VALUES(?,?,?,?,?) ON CONFLICT(resource) DO UPDATE SET person=excluded.person,state=excluded.state,event_id=excluded.event_id,timestamp=excluded.timestamp',
                   (row['resource'],'' if state=='available' else row['person'],state,row['id'],row['timestamp']))

class PersonalEdits:
    def __init__(self,store):self.store=store

    def change(self,domain,rows,updates=None,delete=False):
        table={'wallet':'movements','work':'work_events'}[domain]
        allowed={'wallet':{'amount','category','description'},'work':{'person','resource','description'}}[domain]
        updates=dict(updates or {})
        if set(updates)-allowed:raise ValueError('Campo no editable')
        if 'amount' in updates:updates['amount']=cents(updates['amount'])
        if 'category' in updates and updates['category'] not in CATEGORIES:raise ValueError('Categoría no válida')
        if 'resource' in updates:updates['resource']=resource_key(updates['resource'])
        with self.store.transaction() as db:
            before=[];after=[]
            for expected in rows:
                actual=db.execute(f'SELECT * FROM {table} WHERE id=?',(expected['id'],)).fetchone()
                if actual is None or dict(actual)!=expected or db.execute('SELECT 1 FROM personal_hidden WHERE domain=? AND item_id=?',(domain,expected['id'])).fetchone():
                    raise ValueError('El registro cambió. Revisa la selección antes de continuar.')
                before.append({'row':dict(actual),'hidden':False})
                if delete:
                    db.execute('INSERT INTO personal_hidden VALUES(?,?)',(domain,expected['id']))
                elif updates:
                    db.execute(f"UPDATE {table} SET "+','.join(f'{key}=?' for key in updates)+' WHERE id=?',(*updates.values(),expected['id']))
                after.append({'row':dict(db.execute(f'SELECT * FROM {table} WHERE id=?',(expected['id'],)).fetchone()),'hidden':delete})
            db.execute('INSERT INTO personal_changes(domain,before_json,after_json) VALUES(?,?,?)',(domain,json.dumps(before),json.dumps(after)))
            if domain=='work':rebuild(db)

    def undo(self,domain):
        table={'wallet':'movements','work':'work_events'}[domain]
        with self.store.transaction() as db:
            change=db.execute('SELECT * FROM personal_changes WHERE domain=? AND undone=0 ORDER BY id DESC LIMIT 1',(domain,)).fetchone()
            if not change:raise ValueError('No hay una corrección o eliminación reciente que deshacer.')
            before=json.loads(change['before_json']);after=json.loads(change['after_json'])
            for old,new in zip(before,after):
                row=new['row'];actual=db.execute(f'SELECT * FROM {table} WHERE id=?',(row['id'],)).fetchone()
                hidden=bool(db.execute('SELECT 1 FROM personal_hidden WHERE domain=? AND item_id=?',(domain,row['id'])).fetchone())
                if actual is None or dict(actual)!=row or hidden!=new['hidden']:raise ValueError('El registro cambió después; no puedo deshacerlo automáticamente.')
                values={k:v for k,v in old['row'].items() if k!='id'}
                db.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?',(*values.values(),row['id']))
                db.execute('DELETE FROM personal_hidden WHERE domain=? AND item_id=?',(domain,row['id']))
            db.execute('UPDATE personal_changes SET undone=1 WHERE id=?',(change['id'],))
            if domain=='work':rebuild(db)
