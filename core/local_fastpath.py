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
def _run(cmd): return subprocess.run(cmd,capture_output=True,text=True,timeout=12)
def _schedule(seconds,title,message):
    title=str(title).replace('"',"'"); message=str(message).replace('"',"'")
    osa=f'display notification "{message}" with title "{title}" sound name "Glass"'
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
    if ("captura" in q and "pantalla" in q) or "screenshot" in q:
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
            item=re.sub(r'^(?:recu[eé]rdame|pon(?:me)?\s+un\s+recordatorio)\s*(?:que|de)?\s*','',raw,flags=re.I)
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
