"""Compatibility entry for the existing deterministic fast path, internal only."""
def local_request(parameters, **context):
    from core.local_fastpath import handle
    result = handle(parameters['text'], personal=False)
    state = 'failed' if not result.ok else 'executed'
    if result.text.startswith('VERIFICADO:'): state = 'verified' if result.ok else 'failed'
    if result.text.startswith(('SOLICITADO:', 'Abriendo ')): state = 'requested'
    return {'handled': result.handled, 'state':state, 'text':result.text, 'data':None}

TOOL = {'name':'local_request','description':'Fast Path determinista interno de compatibilidad.',
        'parameters':{'type':'OBJECT','properties':{'text':{'type':'STRING'}},'required':['text']},
        'handler':local_request,'structured_handler':local_request,'semantic':False}
