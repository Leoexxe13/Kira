from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os,re,subprocess,time
@dataclass
class FastResult:
    handled: bool
    ok: bool=True
    text: str=""
    speak: str=""
    activity: str=""
    private: bool=False
def _run(cmd): return subprocess.run(cmd,capture_output=True,text=True,timeout=12)
def _schedule(seconds,title,message):
    import json
    osa=f'display notification {json.dumps(str(message), ensure_ascii=False)} with title {json.dumps(str(title), ensure_ascii=False)} sound name "Glass"'
    py="import time,subprocess;"+f"time.sleep({int(seconds)});"+f"subprocess.Popen(['osascript','-e',{osa!r}]);"+"subprocess.Popen(['afplay','/System/Library/Sounds/Glass.aiff'])"
    subprocess.Popen([os.sys.executable,"-c",py],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
def _dur(q):
    for pat,m in ((r'(\d+)\s*(?:segundos?|s)\b',1),(r'(\d+)\s*(?:minutos?|min)\b',60),(r'(\d+)\s*(?:horas?|h)\b',3600)):
        z=re.search(pat,q)
        if z:return int(z.group(1))*m
    return None
def handle(text):
    raw=str(text or "").strip(); q=raw.lower()
    if not q:return FastResult(False)
    if any(x in q for x in ("qué hora","que hora","dime la hora","hora es")):
        h=datetime.now().strftime("%I:%M %p").lstrip("0"); return FastResult(True,True,f"Son las {h}.",f"Son las {h}.","LOCAL // hora")
    if re.fullmatch(r'(?:guarda|guardar) (?:una )?(?:captura de pantalla|screenshot)',q):
        out=Path.home()/"Desktop"/f"KIRA_Captura_{datetime.now():%Y%m%d_%H%M%S}.png"; r=_run(["screencapture","-x",str(out)]); ok=r.returncode==0 and out.exists()
        return FastResult(True,ok,f"Captura guardada en el Escritorio: {out.name}" if ok else "No pude crear la captura.","Listo. Guardé la captura en el Escritorio." if ok else "No pude crear la captura.","LOCAL // captura")
    if "temporizador" in q:
        s=_dur(q)
        if not s:return FastResult(True,False,"Dime cuánto debe durar el temporizador.","Dime cuánto debe durar.","LOCAL // temporizador")
        _schedule(s,"KIRA // TEMPORIZADOR","El temporizador terminó."); nice=f"{s} segundos" if s<60 else f"{s//60} minutos"
        return FastResult(True,True,f"Temporizador de {nice} iniciado.",f"Temporizador de {nice} iniciado.","LOCAL // temporizador")
    if "recordatorio" in q or q.startswith(("recuérdame","recuerdame")):
        s=_dur(q)
        if s:
            item=re.sub(r'^(?:recu[eé]rdame|pon(?:me)?\s+un\s+recordatorio)\s*(?:(?:que|de)\b)?\s*','',raw,flags=re.I)
            item=re.sub(r'\b(?:en|dentro de)\s+\d+\s*(?:segundos?|minutos?|horas?|s|min|h)\b.*$','',item,flags=re.I).strip(" ,.") or "eso"
            _schedule(s,"KIRA // RECORDATORIO",f"Recuerda: {item}")
            return FastResult(True,True,f"Recordatorio creado: {item}.",f"Listo. Te recordaré {item}.","LOCAL // recordatorio")
    apps={"whatsapp":"WhatsApp","chrome":"Google Chrome","spotify":"Spotify","safari":"Safari","terminal":"Terminal","vscode":"Visual Studio Code","finder":"Finder","correo":"Mail"}
    if q.startswith(("abre ","abrir ","inicia ","lanza ")):
        for k,a in apps.items():
            if k in q:
                r=_run(["open","-a",a]); ok=r.returncode==0
                return FastResult(True,ok,f"Abriendo {a}." if ok else f"No pude abrir {a}.",f"Abriendo {a}." if ok else f"No pude abrir {a}.",f"LOCAL // abrir {a}")
    if any(x in q for x in ("pon música","pon musica","reproduce música","reproduce musica")):
        _run(["open","-a","Spotify"]); time.sleep(.15); r=_run(["osascript","-e",'tell application "Spotify" to play']); ok=r.returncode==0
        return FastResult(True,ok,"Reproduciendo música en Spotify." if ok else "Abrí Spotify, pero no pude iniciar la reproducción.","Reproduciendo música." if ok else "Abrí Spotify, pero no pude iniciar la reproducción.","LOCAL // Spotify")
    m=re.search(r'(?:volumen|audio).{0,12}?(\d{1,3})',q)
    if m:
        level=max(0,min(100,int(m.group(1)))); r=_run(["osascript","-e",f"set volume output volume {level}"]); ok=r.returncode==0
        return FastResult(True,ok,f"Volumen al {level}%." if ok else "No pude cambiar el volumen.",f"Volumen al {level}%." if ok else "No pude cambiar el volumen.","LOCAL // volumen")
    return FastResult(False)

# Shared deterministic operations run before online interpretation.
_legacy_handle = handle

def _result(text, activity='LOCAL // operación'):
    from core.tool_feedback import classify_result
    _,failed,_ = classify_result('local',text)
    return FastResult(True,not failed,text,text,activity)

def _local_operations(raw):
    from core.resource_resolver import resolve_path
    from core.operational_context import CONTEXT
    from actions import file_controller as files
    from actions import kira_tasks as tasks
    q=raw.strip().rstrip('.')
    low=q.lower()
    # A continuation such as ".pdf" filters the folder/results from the
    # immediately preceding Downloads/Documents query instead of becoming a
    # new, provider-dependent conversation.
    if re.fullmatch(r'\.[a-z0-9]{2,8}', low):
        folder = CONTEXT.get('last_folder')
        if folder:
            return _result(files.find_files(name=low, path=folder))
    if re.fullmatch(r'dentro (?:crea|crear) (?:un )?txt', low):
        q='dentro de esa carpeta crea un txt llamado Nuevo archivo'
        low=q.lower()
    elif low.startswith('dentro crea '):
        q='dentro de esa carpeta '+q[7:]
        low=q.lower()
    # Task mutations are unambiguous; never send them through a provider first.
    from core.text_tasks import create_or_list
    task = create_or_list(q, tasks.kira_tasks)
    if task is not None:return _result(task)
    task=re.fullmatch(r'(completa|elimina|borra|marca completada|marca como completada) (?:la )?(?:tarea )?(.+)',q,re.I)
    if task and ('tarea' in low or 'completa' in low):
        items=tasks._load(); wanted=task[2].strip('"')
        candidates=[t for t in items if str(t.get('id'))==wanted or str(t.get('text','')).casefold()==wanted.casefold()]
        if wanted in ('esa','la','la última','la ultima') and len(items)==1:candidates=items
        if len(candidates)!=1:return _result('No pude identificar una única tarea. Indica su nombre o ID.')
        return _result(tasks.kira_tasks({'action':'delete' if task[1].lower() in ('elimina','borra') else 'complete','id':candidates[0]['id']}))
    # Make a folder without inventing a new sibling for subsequent references.
    m=re.fullmatch(r'(?:crea|créame|creame|crear) (?:una )?carpeta(?: en (?:el )?(escritorio|desktop|descargas|downloads|documentos))?(?: (?:llamada|con (?:el )?nombre) (.+))?',q,re.I)
    if m:
        name=(m[2] or 'Nueva carpeta').strip('"')
        if name.lower() in ('que quieras','el que quieras'):name='Nueva carpeta'
        if Path(name).name!=name:return _result('No pude usar ese nombre de carpeta; indica un nombre sin ruta.')
        return _result(files.create_folder(m[1] or 'desktop',name),'LOCAL // carpeta verificada')
    # Explicit destination is required; absent references never default to Desktop.
    m=re.fullmatch(r'(?:dentro de |en )(?:esa carpeta|la que acabamos de crear|la nueva carpeta|ahí|ahi)[, ]+(?:crea|créame|creame) (?:un )?(?:archivo |txt |archivo txt )?(?:llamado )?(.+?)(?: y (?:pon|escribe)(?: dentro)? (.+))?',q,re.I)
    if m:
        folder=resolve_path('esa carpeta');name=m[1].strip('"');content=(m[2] or '').strip('"')
        if 'txt' in low and not Path(name).suffix:name+='.txt'
        if Path(name).name!=name:return _result('No pude usar ese nombre de archivo; indica un nombre sin ruta.')
        return _result(files.create_file(str(folder),name,content),'LOCAL // archivo verificado')
    m=re.fullmatch(r'(?:crea|crear) (?:un )?archivo (.+?) en (.+?)(?: con (?:el )?contenido (.+))?',q,re.I)
    if m:return _result(files.create_file(str(resolve_path(m[2])),m[1].strip('"'),m[3] or ''))
    # A natural query can lead with the object instead of the verb:
    # “archivos .zip en descargas”, “muéstrame los PDF de Downloads”, etc.
    m=re.search(r'\b(?:archivos?|ficheros?)\b.*?(?:\.([a-z0-9]{2,5})\b|\b(zip|pdf|txt|csv|docx?|xlsx?|imagenes?|fotos?)\b).*?\b(descargas|downloads|escritorio|desktop|documentos)\b',low,re.I)
    if m and not re.match(r'^(?:mueve|copia)\b', low):
        ext='.'+(m[1] or m[2]).lstrip('.')
        folder=resolve_path(m[3]); CONTEXT.remember('last_folder', folder)
        return _result(files.find_files(name=ext,path=folder))
    m=re.search(r'\b(?:mis|las|los)\s+ultim(?:as|os)\s+descargas?\b|\bdescargas?\s+recientes\b',low,re.I)
    if m:
        folder=resolve_path('downloads'); CONTEXT.remember('last_folder', folder)
        return _result(files.list_files('downloads'))
    if low in ('lo pusiste fuera de la carpeta','lo pusiste fuera','muévelo a esa carpeta','muevelo a esa carpeta'):
        source=CONTEXT.get('last_file');target=CONTEXT.get('last_folder')
        if not source or not target:return _result('No pude identificar ambos recursos. Indica el archivo y la carpeta destino.')
        if source.parent==target:return _result(f'VERIFICADO: el archivo ya está en {source}')
        return _result(files.move_file(str(source),destination=str(target)))
    m=re.fullmatch(r'(mueve|copia|renombra) (.+?) (?:a|como) (.+)',q,re.I)
    if m:
        source=resolve_path(m[2]);destination=m[3].removeprefix('el ').removeprefix('la ')
        if m[1].lower()=='renombra':return _result(files.rename_file(str(source),new_name=destination.strip('"')))
        fn=files.move_file if m[1].lower()=='mueve' else files.copy_file
        return _result(fn(str(source),destination=str(resolve_path(destination))))
    m=re.fullmatch(r'(?:lista|muestra|listar|ens[eé]ñame|mu[eé]strame) (?:mis |las |los )?(downloads|descargas|desktop|escritorio|documentos)',q,re.I)
    if m:return _result(files.list_files(m[1]))
    m=re.fullmatch(r'(?:abre|abrir|mu[eé]strame|muestra) (?:la carpeta de )?(downloads|descargas|desktop|escritorio|documentos)',q,re.I)
    if m:
        target=resolve_path(m[1])
        if not target.exists(): return _result(f'No existe la carpeta {target}.')
        result=_run(['open',str(target)])
        if result.returncode: return _result('No pude abrir la carpeta.')
        CONTEXT.resource(target,opened=True)
        return _result(f'Carpeta abierta: {target.name}.')
    m=re.fullmatch(r'(?:busca|encuentra) (.+?) en (?:mis |las |el )?(descargas|downloads|escritorio|documentos)',q,re.I)
    if m:return _result(files.find_files(name=m[1].strip('"'),path=m[2]))
    m=re.fullmatch(r'(?:pon|usa) (.+?) (?:de|como) fondo(?: de pantalla)?',q,re.I)
    if m:
        from actions.desktop import set_wallpaper
        return _result(set_wallpaper(str(resolve_path(m[1]))))
    if low in ('abre eso','abre ese archivo','abre esa carpeta','abre la última descarga'):
        ref='última descarga' if 'descarga' in low else q[5:]
        target=resolve_path(ref)
        r=_run(['open',str(target)])
        if r.returncode:return _result('No pude abrir el recurso: '+r.stderr.strip())
        CONTEXT.resource(target,opened=True)
        return _result(f'SOLICITADO: abrir {target}.')
    if low in ('estado del sistema','cpu','ram','métricas','metricas'):
        import psutil
        return _result(f'CPU: {psutil.cpu_percent()}%. RAM: {psutil.virtual_memory().percent}%.')
    # Bounded arithmetic AST, no eval/calls/attributes/exponentiation.
    if re.fullmatch(r'(?:calcula )?[\d\s.+*/()%\-]+',low) and any(c in low for c in '+*/%-'):
        import ast,operator
        expr=ast.parse(low.removeprefix('calcula '),mode='eval')
        ops={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod}
        if len(list(ast.walk(expr)))>40:return _result('No pude calcular una expresión tan larga.')
        def calc(n):
            if isinstance(n,ast.Constant) and type(n.value) in (int,float):return n.value
            if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub):return -calc(n.operand)
            if isinstance(n,ast.BinOp) and type(n.op) in ops:return ops[type(n.op)](calc(n.left),calc(n.right))
            raise ValueError('Operador no permitido')
        return _result(str(calc(expr.body)))
    return FastResult(False)

def handle(text, personal=True):
    raw=str(text or '').strip()
    try:
        # One language layer is shared by typed commands, STT transcripts and
        # the Personal HUB. It is deliberately ahead of legacy exact phrases.
        if personal:
            from core.intent_interpreter import run as interpret_and_run
            from core.personal_store import PersonalStore
            interpreted = interpret_and_run(raw, PersonalStore())
            if interpreted.handled:
                return FastResult(True, interpreted.state == 'verified', interpreted.text,
                                  interpreted.text, interpreted.text.splitlines()[0], True)
            from core.personal_commands import handle as personal_handle
            personal = personal_handle(raw)
            if personal.handled:
                return FastResult(True, personal.state != 'failed', personal.text, personal.text,
                                  personal.text.splitlines()[0], True)
        result=_local_operations(raw)
        if result.handled:return result
        # Avoid intercepting complex app instructions such as "abre el chat de...".
        if raw.lower().startswith(('abre ','abrir ','inicia ','lanza ')):
            if not re.fullmatch(r'(?:abre|abrir|inicia|lanza) (?:whatsapp|chrome|spotify|safari|terminal|vscode|finder|correo)',raw,re.I):
                return FastResult(False)
        return _legacy_handle(raw)
    except Exception as e:
        return _result(f'No pude completar la operación local: {type(e).__name__}: {e}')
