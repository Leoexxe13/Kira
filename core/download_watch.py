"""Poll file metadata, wait for stability, ignore incomplete downloads. No content reads."""
from pathlib import Path
from core.operational_context import CONTEXT

class DownloadWatcher:
    def __init__(self,directory=None,mode='record',context=None):
        self.directory=Path(directory) if directory else Path.home()/'Downloads'
        self.mode=mode if mode in ('record','notify','off') else 'record'
        self.context=context or CONTEXT
        self.seen=self._snapshot();self.pending={}
    def _snapshot(self):
        try:
            result={}
            for p in self.directory.iterdir():
                if not p.is_file() or p.name.startswith('.') or p.suffix.lower() in ('.part','.download','.crdownload','.tmp'):continue
                try:
                    st=p.stat();result[str(p.resolve())]=(st.st_size,st.st_mtime_ns)
                except OSError:pass
            return result
        except OSError:return {}
    def poll(self):
        if self.mode=='off':return []
        now=self._snapshot();events=[]
        for path,signature in now.items():
            if self.seen.get(path)==signature:continue
            if self.pending.get(path)==signature:
                self.context.resource(path)
                self.context.remember('last_download',path)
                self.seen[path]=signature
                events.append(Path(path))
        self.pending={p:s for p,s in now.items() if self.seen.get(p)!=s}
        return events
