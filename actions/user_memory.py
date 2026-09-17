"""Scoped memory tool. The model never supplies the owner's identity."""
from memory.user_memory import active_memory

def run(parameters):
    memory=active_memory()
    action=parameters['action']
    if action=='recall':
        data=memory.recall(parameters.get('query',''))
        text='\n'.join(f"{r['name']}: {r['content']}" for r in data) or 'No hay recuerdos coincidentes.'
    elif action=='remember':
        data=memory.remember(parameters['category'],parameters['key'],parameters['value'],'user_confirmed')
        text='Recuerdo guardado.'
    else:
        data={'deleted':memory.forget(parameters['category'],parameters['key'])}
        text='Recuerdo eliminado. El historial de conversación y los registros no se han borrado.' if data['deleted'] else 'No encontré ese recuerdo.'
    return {'state':'verified','text':text,'data':data}

TOOL={'name':'user_memory','description':'Consulta recuerdos propios. Guarda o corrige únicamente hechos y preferencias explícitamente confirmados por el usuario; nunca inferencias ni credenciales. Elimina recuerdos cuando lo pide el usuario.',
      'parameters':{'type':'OBJECT','properties':{
          'action':{'type':'STRING','enum':['recall','remember','forget']},
          'category':{'type':'STRING','enum':['identity','preferences','projects','relationships','wishes','notes']},
          'key':{'type':'STRING'},'value':{'type':'STRING'},'query':{'type':'STRING'}},'required':['action']},
      'handler':lambda parameters:run(parameters)['text'],'structured_handler':run,
      'safe_actions':('recall',)}
