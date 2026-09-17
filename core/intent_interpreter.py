"""Local intent -> entities -> references -> confirmation -> verified service.

No SQL/tool names are accepted from language models. Domains beyond personal
records are left to their existing guarded routers. No network calls by default.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
import json
from core.wallet import Wallet, cents, money, normalized, category_for, CATEGORIES
from core.work import Work
from core.spanish_numbers import extract_money
from core.intent_context import context_for
from core.personal_edits import PersonalEdits

INTENTS={'CREATE','READ','UPDATE','DELETE','COMPLETE','ASSIGN','RETURN','MARK_PAID','LIST','SUMMARIZE','FILTER','UNDO'}
@dataclass
class Plan:
    intent: str
    domain: str
    entities: dict = field(default_factory=dict)
    confidence: str = 'HIGH'
    question: str = ''

@dataclass
class Result:
    handled: bool
    text: str = ''
    state: str = 'verified'
    domain: str = ''
    intent: str = ''
    confidence: str = 'HIGH'

# Small verb families and entity grammars, independent of domain execution.
VERBS={
 'DELETE':r'\b(borra\w*|elimina\w*|quita\w*)\b',
 'UNDO':r'\b(deshaz|deshacer|revierte)\b',
 'UPDATE':r'\b(cambia\w*|corrige\w*|eran|era|fue)\b',
 'MARK_PAID':r'\b(pague|pago|pagar|gaste|compre|sald[eoa]|cobraron|di)\b|como pagado',
 'LIST':r'\b(muestra\w*|lista\w*)\b',
}
ALIASES={'uni':'universidad','universitaria':'universidad'}
def concept(text):
    words=[ALIASES.get(t,t) for t in normalized(text).split()]
    stop={'de','del','la','el','los','las','lo','a','para','en','por','un','una','hoy','ayer','ya','pesos','peso'}
    return ' '.join(w for w in words if w not in stop).strip(' .,:;')

def interpret(text,ctx):
    low=normalized(text).strip(' ¿?¡!.')
    low=re.sub(r'^kira[, :] *','',low)
    explicit_work=bool(re.search(r'\b(trabajo|eventos?|fichas?|turno|vehiculos?)\b',low))
    explicit_wallet=bool(re.search(r'\b(gastos?|pagos?|billetera|movimientos?|ingresos?|universidad|uni|dinero|pesos)\b',low))
    work_relation=bool((re.search(r'\b(?:ficha|vehiculo)\b',low) and re.search(r'\b(?:la\s+)?\d+\b',low)) or re.search(r'\bla\s+\d{1,3}\s+(?:a|tiene)\b',low))
    financial=bool(re.search(r'\b(cobre|pagaron|recibi|depositaron|pague|gaste|compre|cobraron|aparta\w*|guarde|ahorrar|sald[eoa])\b|voy a (?:pagar|gastar)|le di\s+',low))
    domain='work' if explicit_work or work_relation else ('wallet' if explicit_wallet or financial else (ctx.page or ctx.domain))
    for intent in ('UNDO','DELETE'):
        if re.search(VERBS[intent],low):
            if re.search(r'\b(archivo|carpeta|whatsapp|chat|aplicacion)\b',low):return None
            if domain not in ('work','wallet'):return None
            return Plan(intent,domain,{'reference':low})
    correction=bool(re.search(VERBS['UPDATE'],low) or re.search(r'\bponlo como pagado\b',low))
    if correction and domain in ('work','wallet'):
        fields={}
        if domain=='wallet':
            if 'pagado' in low:return Plan('MARK_PAID',domain,{'reference':low})
            amount,_,approx=extract_money(low)
            if approx:return Plan('UPDATE',domain,confidence='LOW',question='¿Cuánto fue exactamente?')
            if amount:fields['amount']=amount
            cat=category_for(concept(low.split(', no')[0].split(' no ')[0]))
            if cat!='Otros':fields['category']=cat
            name=re.search(r'(?:nombre|concepto|descripcion)\s+(?:a|por)\s+(.+)',low)
            if name:fields['description']=name[1]
        else:
            nums=re.findall(r'\b\d+\b',low)
            if nums and ('ficha' in low or re.search(r'\b(?:era|eran)\b',low)):
                fields['resource']=nums[-1]
            person=re.search(r'\b(?:cambia|cambiar)\s+(.+?)\s+por\s+(.+)',low)
            if person and not nums:fields['person']=person[2].title()
            person2=re.search(r'\bera\s+([a-z]+)(?:,?\s+no\s+[a-z]+)?$',low)
            if person2 and not nums:fields['person']=person2[1].title()
        return Plan('UPDATE',domain,{'updates':fields,'reference':low},'HIGH' if fields else 'LOW', '' if fields else '¿Qué dato quieres corregir y cuál es el valor correcto?')
    # Resource numbers are extracted before any money interpretation.
    if domain=='work':
        resource=re.search(r'\b(?:ficha\s+|la\s+)([a-z]*-?\d+)\b',low)
        if resource:
            rid=resource[1]
            if 'quien' in low or low.startswith('consulta'):return Plan('READ','work',{'resource':rid})
            if re.search(r'\b(devol\w*|volvio)\b',low):return Plan('RETURN','work',{'resource':rid})
            person=re.search(r'\b(?:a|tiene)\s+([a-z][a-z ]+)$',low)
            if not person:person=re.search(r'^([a-z][a-z ]+?)\s+(?:se llevo|tiene)',low)
            if person:return Plan('ASSIGN','work',{'resource':rid,'person':person[1].title()})
        # Existing shift/incidence service grammar remains available.
        if re.search(VERBS['LIST'],low):return Plan('LIST','work',{'reference':low})
        return None
    if domain=='wallet' and financial:
        amount,remainder,approx=extract_money(low)
        if approx:return Plan('CREATE','wallet',confidence='LOW',question='¿Cuánto fue exactamente?')
        kind=('income' if re.search(r'\b(cobre|pagaron|recibi|depositaron)\b',low) else
              'planned' if re.search(r'\bvoy a\b',low) else
              'reserve' if re.search(r'\b(apart\w*|guarde|ahorrar)\b',low) else 'expense')
        remainder=re.sub(r'^(?:hoy\s+)?(?:ya\s+)?(?:voy a\s+)?(?:me\s+|le\s+)?(?:cobre|pagaron|recibi|depositaron|pague|pagar|gastar|gaste|compre|cobraron|di|aparta\w*|guarde|salde|registre)\b','',remainder).strip()
        detail=concept(remainder)
        intent='MARK_PAID' if kind=='expense' else 'CREATE'
        return Plan(intent,'wallet',{'amount':amount,'kind':kind,'description':detail,'reference':low})
    if domain=='wallet' and re.search(VERBS['LIST'],low):return Plan('LIST',domain,{'reference':low})
    return None

def labels(domain,rows):
    return '\n'.join(f"{i}. "+(f"{r['description'] or r['category']} · {money(r['amount'],r['currency'])} · {r['created'][:16]}" if domain=='wallet' else f"{r['description']} · {r['person']} · {r['resource']} · {r['timestamp'][:16]}") for i,r in enumerate(rows,1))

def select_rows(plan,ctx,rows,now):
    ref=plan.entities.get('reference','')
    ids=[]
    if 'aqui' in ref or 'seleccionad' in ref:
        ids=[int(i) for i in (ctx.selected or ctx.visible)]
    elif re.search(r'\b(segundo|primero|tercero|arriba|opcion)\b',ref):
        position=2 if 'segundo' in ref else 3 if 'tercero' in ref else 1
        match=re.search(r'opcion\s+(\d+)',ref)
        if match:position=int(match[1])
        source=ctx.results or ctx.visible
        ids=source[position-1:position] if position>0 else []
        return [r for r in rows if r['id'] in ids]
    elif re.search(r'\b(esos dos|los dos|estos tres)\b',ref):
        count=3 if 'tres' in ref else 2
        source=ctx.selected or ctx.results
        if len(source)!=count:return []
        ids=source
    elif 'hoy' in ref:
        return [r for r in rows if (r.get('created') or r['timestamp'])[:10]==now[:10]]
    elif 'ahorita' in ref:
        return [r for r in rows if datetime.fromisoformat(now)-datetime.fromisoformat(r.get('created') or r['recorded_at']) < timedelta(minutes=10)]
    elif 'prueba' in ref:
        return [r for r in rows if 'prueba' in normalized(r['description'])]
    elif 'ultimo' in ref:
        return rows[:1]
    else:
        detail=plan.entities.get('description')
        if not detail:
            detail=next((normalized(c) for c in CATEGORIES if normalized(c) in concept(ref)),None)
        if detail:
            return [r for r in rows if concept(r['description'])==concept(detail) or ('category' in r and normalized(r['category'])==concept(detail))]
        # Explicit old holder/resource constrains corrections; no new duplicate.
        if plan.domain=='work':
            match=re.search(r'cambia\s+(.+?)\s+por',ref)
            if match:return [r for r in rows if normalized(r['person'])==match[1]]
            match=re.search(r'no era (?:la )?(\d+)',ref)
            if match:return [r for r in rows if r['resource']==match[1]]
        ids=[int(i) for i in ctx.selected] if ctx.page==plan.domain else []
        if not ids and plan.domain in ctx.last:ids=[ctx.last[plan.domain]]
    return [r for r in rows if r['id'] in ids]

def run(text,store,fallback=None):
    ctx=context_for(store)
    with ctx.lock:
        ctx.fresh()
        low=normalized(text).strip(' ¿?¡!.')
        def result(message,state='verified',plan=None):
            return Result(True,message,state,plan.domain if plan else ctx.domain,plan.intent if plan else '',plan.confidence if plan else 'HIGH')
        pending=ctx.pending
        if pending and low in ('no','cancelar','cancela','no borres'):
            ctx.pending=None;return result('Cancelado.','pending')
        if pending and low in ('si','si confirma','confirmo','adelante'):
            plan,chosen,mode=pending
            if mode!='confirm':return result('Selecciona primero el registro que quieres usar.','pending',plan)
            ctx.pending=None
            PersonalEdits(store).change(plan.domain,chosen,delete=True)
            return result('Eliminado.' if len(chosen)==1 else f'Eliminados {len(chosen)} registros.',plan=plan)
        if pending and pending[2]=='choose':
            plan,options,_=pending
            match=re.fullmatch(r'(?:(?:el|la|opcion)\s+)?(\d+|primero|primera|segundo|segunda|tercero|tercera)',low)
            if match:
                number={'primero':1,'primera':1,'segundo':2,'segunda':2,'tercero':3,'tercera':3}.get(match[1])
                number=number if number else int(match[1])
                if not 1<=number<=len(options):return result(f'Elige una opción entre 1 y {len(options)}.','pending',plan)
                chosen=[options[number-1]];ctx.pending=None
                return execute(plan,chosen,ctx,store)
        plan=interpret(text,ctx)
        if plan is None and fallback is not None:
            plan=structured_fallback(text,fallback)
        if plan is None:return Result(False)
        ctx.pending=None
        ctx.domain=plan.domain
        if plan.question:return result(plan.question,'pending',plan)
        if plan.intent in ('ASSIGN','RETURN','READ'):
            work=Work(store);e=plan.entities
            if plan.intent=='READ':
                _, text = work.describe_holder(e['resource'])
                return result(text,plan=plan)
            eid=work.event('assign' if plan.intent=='ASSIGN' else 'return',str(text),person=e.get('person',''),resource=e['resource'])
            ctx.last['work']=eid
            return result(f"Ficha {e['resource']} "+(f"asignada a {e['person']}." if plan.intent=='ASSIGN' else 'devuelta.'),plan=plan)
        if plan.intent=='UNDO':
            PersonalEdits(store).undo(plan.domain);return result('Deshecho.',plan=plan)
        if plan.intent=='CREATE':
            e=plan.entities
            if not e.get('amount'):return result('¿Cuál es el monto exacto?','pending',plan)
            mid=Wallet(store).record(e['kind'],e['amount'],e['description'])
            ctx.last['wallet']=mid
            return result(f"Registrado: {e['description'] or 'Ingreso'} · {money(cents(e['amount']))}.",plan=plan)
        rows=Wallet(store).movements() if plan.domain=='wallet' else Work(store).events()
        if plan.intent=='LIST':
            ctx.results=[r['id'] for r in rows[:20]]
            return result(labels(plan.domain,rows[:20]) or 'No hay registros.',plan=plan)
        if plan.intent=='MARK_PAID' and plan.entities.get('amount'):
            e=plan.entities
            matches=[r for r in rows if r['state']=='open' and r['kind'] in ('planned','reserve') and (concept(r['description'])==e['description'] or normalized(r['category'])==e['description']) and r['amount']==cents(e['amount'])]
            if not matches:
                mid=Wallet(store).record('expense',e['amount'],e['description'])
                ctx.last['wallet']=mid
                return result(f"Registrado: {e['description'] or 'Gasto'} · {money(cents(e['amount']))}.",plan=plan)
            chosen=matches
        else:chosen=select_rows(plan,ctx,rows,store.now())
        if plan.intent=='MARK_PAID':chosen=[r for r in chosen if r.get('kind') in ('planned','reserve') and r['state']=='open']
        if not chosen:return result('¿A qué registro te refieres?' if plan.intent!='MARK_PAID' else 'No encuentro un compromiso pendiente compatible. ¿Qué monto pagaste exactamente?','pending',plan)
        bulk=plan.intent=='DELETE' and bool(re.search(r'\b(todos|eventos|gastos|esos dos|los dos|estos tres|ahorita|seleccionados)\b',low))
        if len(chosen)>1 and not bulk:
            ctx.pending=(plan,chosen,'choose');ctx.results=[r['id'] for r in chosen]
            return result(labels(plan.domain,chosen)+'\n¿Cuál quieres usar?','pending',plan)
        return execute(plan,chosen,ctx,store)

def execute(plan,chosen,ctx,store):
    def reply(text,state='verified'):return Result(True,text,state,plan.domain,plan.intent,plan.confidence)
    # Every selected snapshot must still be current before execution.
    current=Wallet(store).movements() if plan.domain=='wallet' else Work(store).events()
    if any(row not in current for row in chosen):return reply('El registro cambió. Vuelve a seleccionarlo.','pending')
    if plan.intent=='DELETE':
        ctx.pending=(plan,chosen,'confirm')
        return reply(labels(plan.domain,chosen)+f'\n¿Elimino {len(chosen)} registro(s)?','pending')
    if plan.intent=='UPDATE':
        PersonalEdits(store).change(plan.domain,chosen,plan.entities['updates'])
        ctx.last[plan.domain]=chosen[-1]['id']
        amount=plan.entities['updates'].get('amount')
        return reply('Corregido'+(f' a {money(cents(amount))}' if amount else '')+'.')
    if plan.intent=='MARK_PAID':
        mid,_=Wallet(store).pay(movement_id=chosen[0]['id'])
        ctx.last['wallet']=mid
        return reply(f"Marcado como pagado: {chosen[0]['description']} · {money(chosen[0]['amount'])}.")
    return reply('¿Qué cambio quieres realizar?','pending')

def structured_fallback(text,callback):
    """Optional explicitly supplied interpreter. Only current text is shared.

    Reject unknown schema and unsupported domains. Untrusted proposals do not
    authorize execution: ask the user to supply the missing detail locally.
    """
    try:
        data=callback(str(text))
        if isinstance(data,str):
            raw=data.strip()
            # Local models often wrap valid JSON in a markdown fence or add a
            # short preface. Extract only the first complete object; prose is
            # never treated as an executable proposal.
            if raw.startswith('```'):
                raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.I|re.S).strip()
            try:
                data=json.loads(raw)
            except json.JSONDecodeError:
                start, end = raw.find('{'), raw.rfind('}')
                if start < 0 or end <= start: raise
                data=json.loads(raw[start:end+1])
        if set(data)-{'intent','domain','entities','confidence'}:return None
        if data.get('intent') not in INTENTS or data.get('domain') not in ('wallet','work'):return None
        domain, intent = data['domain'], data['intent']
        e = data.get('entities', {})
        if not isinstance(e, dict): return None
        if set(e) - {'amount','kind','description','resource','person','reference','updates'}: return None
        confidence = data.get('confidence')
        high = confidence == 'HIGH' or (type(confidence) in (float,int) and .9 <= confidence <= 1)
        if not high:
            return Plan(intent,domain,confidence='LOW',question='¿Qué dato exacto debo registrar o cambiar?')
        # Money must be independently present and exact in the actual request.
        if 'amount' in e:
            amount, _, approximate = extract_money(normalized(text))
            if approximate or amount is None or cents(amount) != cents(e['amount']):
                return Plan(intent,domain,confidence='LOW',question='¿Cuánto fue exactamente?')
        if intent in ('CREATE','MARK_PAID') and domain == 'wallet':
            if e.get('kind') not in ('income','expense','planned','reserve'): return None
            if not isinstance(e.get('description',''),str):return None
            e.setdefault('description','')
        elif intent in ('ASSIGN','RETURN','READ') and domain == 'work':
            if not e.get('resource'):return None
            if str(e['resource']).lower() not in normalized(text): return None
            if intent == 'ASSIGN' and (not e.get('person') or normalized(e['person']) not in normalized(text)):return None
        elif intent == 'UPDATE':
            # Corrections proposed by AI are clarified before mutation.
            return Plan(intent,domain,confidence='LOW',question='¿Qué campo quieres corregir y cuál es su valor exacto?')
        elif intent not in ('DELETE','LIST','UNDO'):return None
        e['reference'] = normalized(text)
        return Plan(intent,domain,e)

    except (ValueError,TypeError,KeyError):return None
