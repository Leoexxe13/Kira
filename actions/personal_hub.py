"""Single existing action-loader entry for explicitly requested local records."""
from core.personal_commands import handle
from core.intent_interpreter import run
from core.personal_store import PersonalStore


def personal_hub(parameters, **_context):
    result = personal_result(parameters, **_context)
    prefix = {'verified':'VERIFICADO', 'failed':'ERROR', 'pending':'PENDIENTE'}[result['state']]
    return f"{prefix}: {result['text']}"


def personal_result(parameters, session_memory=None, **_context):
    command = parameters.get('command', '')
    store = (session_memory or {}).get('store') or PersonalStore()
    result = run(command, store)
    if not result.handled:
        result = handle(command, store)
    if not result.handled:
        return {'handled':False,'state':'pending','text':'¿Qué registro quieres consultar o cambiar?', 'data':None}
    return {'handled':True,'state':result.state,'text':result.text,'data':None,'domain':result.domain,
            'intent':getattr(result,'intent',''),'confidence':getattr(result,'confidence','HIGH')}


TOOL = {
    'name':'personal_hub',
    'description': 'Trabajo y billetera LOCALES. Usa solo una orden explícita del usuario: iniciar/cerrar turno, registrar ficha/incidencia/pendiente, ingreso, gasto, apartado, pago o consulta. No hace pagos ni envíos. No inventes personas ni montos. command debe conservar el texto solicitado. Un pago planeado NO es un gasto real.',
    'parameters': {'type':'OBJECT','properties':{'command':{'type':'STRING','description':'Orden o consulta personal explícita en español'}},'required':['command']},
    'handler': personal_hub,
    'structured_handler': personal_result,
    'semantic': False,
}
