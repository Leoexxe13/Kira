"""Shared filesystem aliases and explicit operational references."""
from pathlib import Path
from core.operational_context import CONTEXT

ALIASES={'downloads':'Downloads','descargas':'Downloads','desktop':'Desktop','escritorio':'Desktop','documents':'Documents','documentos':'Documents','pictures':'Pictures','imagenes':'Pictures','imágenes':'Pictures','music':'Music','música':'Music','musica':'Music','videos':'Movies','home':'','inicio':''}
REFERENCES={'esa carpeta':'last_folder','la que acabamos de crear':'last_created_resource','ese archivo':'last_file','esa imagen':'last_image','la imagen que descargué':'last_download','última descarga':'last_download','ultima descarga':'last_download','ahí':'last_folder','ahi':'last_folder','eso':'last_resource'}

def resolve_path(raw, *, home=None, context=None):
    home=Path(home) if home is not None else Path.home()
    context=context or CONTEXT
    raw=str(raw or '').strip().strip('"')
    if raw.lower() in REFERENCES:
        path=context.get(REFERENCES[raw.lower()])
        if path is None: raise ValueError('No hay un recurso reciente confirmado para esa referencia; indica la ruta.')
        return path
    if raw=='~' or raw.startswith('~/'): return (home/raw[2:]).resolve() if raw!='~' else home.resolve()
    p=Path(raw)
    if p.is_absolute(): return p.resolve()
    parts=p.parts
    if parts and parts[0].lower() in ALIASES:
        return home.joinpath(ALIASES[parts[0].lower()],*parts[1:]).resolve()
    return (home/p).resolve()
