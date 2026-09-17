"""Small textual adapter for the existing guarded WhatsApp registry action."""
import json
import re
from core.intent_interpreter import Result

def whatsapp_result(raw):
    """Translate backend evidence without changing recipient resolution/guards."""
    tag, _, body = str(raw).partition(':')
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    reason = str(data.get('reason') or body.strip() or 'WhatsApp no confirmó la operación.')
    if tag == 'WHATSAPP_UNVERIFIED' and data.get('query') and 'candidates' in data and not data.get('expected'):
        candidates = data['candidates']
        if data.get('selection_supported') and candidates:
            reason = '\n'.join(c.get('label') or f"{i}. {c.get('name') or 'Chat sin nombre'}" for i,c in enumerate(candidates,1)) + '\n¿Cuál quieres usar?'
        elif data.get('match'):
            return {'state':'failed','text':'WhatsApp no pudo confirmar la apertura del chat.','data':data}
        else:
            reason = 'No encontré un chat con ese nombre.'
        return {'state':'pending','text':reason,'data':data}
    if tag == 'WHATSAPP_VERIFIED_SEARCH' and data.get('match') and not data.get('selection_supported'):
        return {'state':'verified','text':f"Chat encontrado: {data['match'].get('name') or data.get('query') }.",'data':data}
    if tag == 'WHATSAPP_VERIFIED_SEARCH' and data.get('selection_supported'):
        labels = [c.get('label') or f"{i}. {c.get('name') or 'Chat sin nombre'}"
                  for i,c in enumerate(data.get('candidates', []), 1)]
        return {'state':'pending','text':'\n'.join(labels + ['¿Cuál quieres usar?']), 'data':data}
    if tag.startswith('WHATSAPP_VERIFIED_'):
        text = {'WHATSAPP_VERIFIED_OPEN':'WhatsApp abierto.',
                'WHATSAPP_VERIFIED_CLOSED':'WhatsApp cerrado.',
                'WHATSAPP_VERIFIED_CHAT_OPEN':'Chat abierto.',
                'WHATSAPP_VERIFIED_SENT':'Mensaje enviado al chat verificado.'}.get(tag)
        if text is None:
            if 'chats' in data:
                text = '\n'.join(c.get('name') or 'Chat sin nombre' for c in data['chats']) or 'No hay chats en el resultado.'
            elif 'messages' in data or isinstance(data.get('message'), dict):
                messages = data.get('messages', [data.get('message')])
                text = '\n'.join(str(m.get('body') or m.get('text') or '[Mensaje sin texto]') for m in messages if m) or 'No hay mensajes en el resultado.'
            elif tag == 'WHATSAPP_VERIFIED_STATUS':
                text = 'WhatsApp conectado.' if data.get('authenticated') else 'WhatsApp no confirmó la sesión.'
            else:
                text = 'Consulta de WhatsApp completada.'
        context = {}
        if data.get('id') and isinstance(data.get('chat'), str):
            context['last_whatsapp_chat'] = {'id':data['id'], 'name':data['chat']}
        return {'state':'verified','text':text,'data':data,'context':context}
    if tag == 'WHATSAPP_SEND_REQUESTED':
        return {'state':'executed','text':'Envío solicitado; WhatsApp todavía no lo confirmó.','data':data}
    return {'state':'failed','text':reason,'data':data}

class WhatsAppText:
    def __init__(self): self.pending = False

    def handle(self, text, registry):
        params = None
        option = re.fullmatch(r'(?:el |la |opción |opcion )?(primero|segundo|tercero|\d+)', text.strip(' .'), re.I)
        if self.pending and option:
            value = option[1].lower()
            params = {'action':'select','candidate_index':{'primero':1,'segundo':2,'tercero':3}.get(value, int(value) if value.isdigit() else 0)}
        # Free-form recipient/message requests belong to the semantic interpreter.
        # A greedy recipient regex also consumes the rest of compound requests.
        match = re.fullmatch(r'(?:abre|abrir) (?:el )?chat (?:de|con) (.+?)[.]?', text, re.I)
        if match and ' y ' not in match[1].casefold():
            params = {'action':'select','chat':match[1].strip()}
        if text.strip(' .').lower() in ('abre whatsapp','abrir whatsapp'): params={'action':'open'}
        if text.strip(' .').lower() in ('cierra whatsapp','cerrar whatsapp'): params={'action':'close'}
        match = re.fullmatch(r'(?:abre|abrir) (?:el )?chat (?:de|con) (.+?)\s+y\s+(?:escr[ií]bele|dile|m[aá]ndale|env[ií]ale)\s+(?:que\s+)?(.+?)[.]?', text, re.I)
        if match:
            params = {'action':'send','chat':match[1].strip(),'message':match[2].strip()}
        if params is None:return None
        if registry is None or not registry.has('whatsapp_web'):
            return Result(True,'WhatsApp no está registrado como herramienta disponible.','failed','whatsapp')
        raw=registry.run('whatsapp_web',params)
        try: payload=json.loads(raw.split(':',1)[1])
        except (ValueError,IndexError,TypeError):payload={}
        candidates=payload.get('candidates',[])
        if candidates:
            self.pending=True
            lines=[]
            for i,c in enumerate(candidates,1):
                name=c.get('label') or c.get('name') or c.get('id','Chat sin nombre')
                lines.append(f'{i}. {name}')
            return Result(True,'\n'.join(lines)+'\n¿Cuál quieres usar?','pending','whatsapp')
        if raw.startswith('WHATSAPP_VERIFIED_'):
            self.pending=False
            message='Chat abierto.' if 'CHAT_OPEN' in raw else 'WhatsApp abierto.'
            if 'SENT:' in raw:message='Mensaje enviado al chat verificado.'
            elif 'CLOSED:' in raw:message='WhatsApp cerrado.'
            elif params['action']=='read':message=json.dumps(payload,ensure_ascii=False)
            return Result(True,message,'verified','whatsapp')
        return Result(True,str(payload.get('reason') or 'WhatsApp no confirmó la operación. Revisa el destinatario o vuelve a intentarlo.'),'pending','whatsapp')
