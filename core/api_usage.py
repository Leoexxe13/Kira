from pathlib import Path
import json,threading
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"memory"/"api_usage.json"
LOCK=threading.Lock()
def _load():
    try:
        if PATH.exists():
            x=json.loads(PATH.read_text(encoding="utf-8"))
            if isinstance(x,dict): return x
    except Exception: pass
    return {"local":0,"groq":0,"gemini":0,"errors":0,"day":datetime.now().strftime("%Y-%m-%d")}
def _save(x):
    PATH.parent.mkdir(parents=True,exist_ok=True)
    PATH.write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding="utf-8")
def record(kind,ok=True):
    with LOCK:
        x=_load(); today=datetime.now().strftime("%Y-%m-%d")
        if x.get("day")!=today: x={"local":0,"groq":0,"gemini":0,"errors":0,"day":today}
        if kind in ("local","groq","gemini"): x[kind]=int(x.get(kind,0))+1
        if not ok: x["errors"]=int(x.get("errors",0))+1
        _save(x)
def summary():
    x=_load(); x["api_calls"]=int(x.get("groq",0))+int(x.get("gemini",0)); x["paid_budget_usd"]=0.0; return x
def text():
    x=summary()
    return f"Uso de hoy: {x['local']} acciones locales, {x['groq']} consultas a Groq y {x['gemini']} a Gemini. Presupuesto de APIs pagas: US$0."
