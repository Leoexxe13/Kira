from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def get_skills():
    out=[]
    for folder,kind in ((ROOT/"actions","HERRAMIENTA"),(ROOT/"plugins","PLUGIN")):
        if not folder.exists():continue
        for f in sorted(folder.glob("*.py")):
            if not f.name.startswith("_"):out.append({"name":f.stem.replace("_"," ").title(),"type":kind,"status":"DISPONIBLE"})
    return out
def text():
    xs=get_skills()
    return "Habilidades detectadas: "+", ".join(x["name"] for x in xs[:25]) if xs else "No encontré habilidades instaladas."
