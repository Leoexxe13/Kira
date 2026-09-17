"""Typed local services exposed through the same action registry as desktop tools."""
from core.personal_store import PersonalStore
from core.wallet import Wallet, money
from core.work import Work
from core.personal_edits import PersonalEdits


def personal_records(parameters, session_memory=None):
    p = parameters
    store = (session_memory or {}).get('store') or PersonalStore()
    domain, action = p['domain'], p['action']
    wallet, work = Wallet(store), Work(store)
    service = wallet if domain=='wallet' else work
    rows = lambda: wallet.movements() if domain=='wallet' else work.events()
    mid = None
    if action=='create':
        if domain=='wallet':
            if p.get('kind')=='expense': mid,_ = wallet.pay(p['amount'],p.get('description',''))
            else: mid = wallet.record(p['kind'],p['amount'],p.get('description',''),p.get('category'))
        else:
            mid = work.event(p.get('category','note'),p.get('description',''),person=p.get('person',''),resource=p.get('resource',''))
    elif action in ('assign','return') and domain=='work':
        mid = work.event(action,p.get('description',''),person=p.get('person',''),resource=p['resource'])
    elif action=='read' and domain=='work':
        data,text=work.describe_holder(p['resource'])
        return {'state':'verified','data':data,'text':text}
    elif action in ('update','delete','mark_paid'):
        chosen=[r for r in rows() if r['id'] in p.get('ids',[])]
        if not chosen or len(chosen)!=len(set(p.get('ids',[]))):raise ValueError('La selección ya no existe')
        if action=='mark_paid':
            if domain!='wallet' or len(chosen)!=1:raise ValueError('Selecciona un compromiso')
            mid,_=wallet.pay(movement_id=chosen[0]['id'])
        else:
            PersonalEdits(store).change(domain,chosen,p.get('updates'),delete=action=='delete')
            if action=='delete':
                if any(r['id'] in p['ids'] for r in rows()):raise ValueError('No se verificó la eliminación')
                return {'state':'verified','data':None,'text':f"Eliminados {len(chosen)} registros."}
            mid=chosen[-1]['id']
    elif action=='undo':
        PersonalEdits(store).undo(domain)
        return {'state':'verified','data':rows()[:20],'text':'Deshecho.'}
    elif action=='list':
        data=rows()[:20]
        return {'state':'verified','data':data,'text':'\n'.join(f"{r['id']}: {r['description']}" for r in data) or 'No hay registros.'}
    elif action=='summary':
        return {'state':'verified','data':None,'text':wallet.summary_text() if domain=='wallet' else work.summary()}
    elif domain=='work' and action=='start_shift':
        work.start_shift()
        return {'state':'verified','data':work.active_shift(),'text':'Turno iniciado.'}
    elif domain=='work' and action=='close_shift':
        text=work.close_shift()
        if work.active_shift():raise ValueError('No se verificó el cierre')
        return {'state':'verified','data':None,'text':text}
    elif domain=='work' and action=='complete':
        for eid in p['ids']:work.complete(eid)
        if any(r['id'] in p['ids'] for r in work.pending()):raise ValueError('No se verificó el pendiente')
        return {'state':'verified','data':None,'text':'Pendiente completado.'}
    else:raise ValueError('Operación incompatible con el dominio')
    actual=next((r for r in rows() if r['id']==mid),None)
    if actual is None:raise ValueError('No se verificó el registro en SQLite')
    if domain=='wallet':text=f"Registrado: {actual['description'] or actual['category']} · {money(actual['amount'])}."
    else:text=f"Registrado: {actual['resource']} · {actual['person']} · {actual['category']}."
    return {'state':'verified','data':actual,'text':text,
            'context':{'last_wallet_transaction' if domain=='wallet' else 'last_work_event':actual}}


TOOL={
    'name':'personal_records',
    'description':'Servicios locales tipados de trabajo/billetera. amount en pesos, no centavos. ids provienen de resultados reales. expense concilia un compromiso compatible antes de crear gasto. delete y update preservan historial y reconstruyen tenedores. No realiza pagos bancarios.',
    'parameters':{'type':'OBJECT','required':['domain','action'],'properties':{
        'domain':{'type':'STRING','enum':['wallet','work']},
        'action':{'type':'STRING','enum':['create','assign','return','read','update','delete','mark_paid','list','summary','undo','start_shift','close_shift','complete']},
        'kind':{'type':'STRING','enum':['income','expense','planned','reserve']},
        'amount':{'type':'STRING'},'description':{'type':'STRING'},'category':{'type':'STRING'},
        'resource':{'type':'STRING'},'person':{'type':'STRING'},
        'ids':{'type':'ARRAY','items':{'type':'INTEGER'}},
        'updates':{'type':'OBJECT','properties':{k:{'type':'STRING'} for k in ('amount','category','description','person','resource')}}}},
    'handler':personal_records,'structured_handler':personal_records,
    'safe_actions':('create','assign','return','read','list','summary','start_shift','close_shift','complete','mark_paid'),
}
