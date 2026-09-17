"""Task references and confirmation around the existing offline task service."""
import re
from core.intent_interpreter import Result
from core.wallet import normalized

def create_or_list(text, invoke):
    """Existing deterministic task grammar, shared with the compatibility path."""
    q=text.strip().rstrip('.')
    task=re.fullmatch(r'(?:crea (?:una )?tarea|agrega (?:a )?(?:mis )?pendientes|añade (?:a )?pendientes|tengo que|tengo pendiente)\s*:?\s+(.+)',q,re.I)
    if task:return invoke({'action':'add','text':task[1]})
    task=re.fullmatch(r'(?:agrega|añade) (.+) a (?:mis )?pendientes',q,re.I)
    if task:return invoke({'action':'add','text':task[1]})
    if q.lower() in ('mis pendientes','lista mis tareas','qué tengo pendiente','que tengo pendiente'):
        return invoke({'action':'list'})
    return None

class TextTasks:
    def __init__(self):
        self.pending=None
        self.last=None

    def handle(self,text,registry=None):
        from actions import kira_tasks as tasks
        invoke = (lambda args:registry.run('kira_tasks',args)) if registry is not None else tasks.kira_tasks
        answer = create_or_list(text, invoke)
        if answer is not None:
            return Result(True, answer, 'executed', 'tasks')
        low=normalized(text).strip(' .¿?')
        if self.pending:
            if low in ('no','cancelar','cancela'):
                self.pending=None
                return Result(True,'Cancelado.','pending','tasks')
            if low in ('si','confirmo'):
                old=self.pending;self.pending=None
                if old not in tasks._load():return Result(True,'La tarea cambió. Selecciónala otra vez.','pending','tasks')
                answer=invoke({'action':'delete','id':old['id']})
                verified=all(t['id']!=old['id'] for t in tasks._load())
                return Result(True,answer,'verified' if verified else 'failed','tasks')
        m=re.fullmatch(r'(completa|elimina|borra|edita|cambia) (?:la )?tarea (.+?)(?: (?:a|por) ["“](.+)["”])?',low)
        if not m:return None
        rows=tasks._load();ref=m[2]
        options=[r for r in rows if normalized(r['text'])==ref]
        if ref in ('ultima','la ultima'): options=rows[-1:]
        if len(options)!=1:
            return Result(True,'Indica el nombre de la tarea que quieres cambiar.','pending','tasks')
        row=options[0]
        if m[1] in ('borra','elimina'):
            self.pending=dict(row)
            return Result(True,'¿Elimino la tarea «'+row['text']+'»?','pending','tasks')
        action='complete' if m[1]=='completa' else 'update'
        if action=='update' and not m[3]:return Result(True,'¿Qué texto debe tener la tarea?','pending','tasks')
        answer=invoke({'action':action,'id':row['id'],'text':m[3] or ''})
        updated=next((t for t in tasks._load() if t['id']==row['id']),None)
        verified=bool(updated and (updated.get('done') if action=='complete' else updated['text']==m[3]))
        return Result(True,answer,'verified' if verified else 'failed','tasks')
