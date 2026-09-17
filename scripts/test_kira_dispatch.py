#!/usr/bin/env python3
"""Real dispatcher inspection. --dry-run invokes the model but executes no tool."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.personal_store import PersonalStore
from core.dispatcher import Dispatcher


def main():
    parser=argparse.ArgumentParser(description='Inspección del dispatcher real')
    parser.add_argument('text', nargs='*')
    parser.add_argument('--db',default=None,help='SQLite alternativa para pruebas')
    parser.add_argument('--interactive',action='store_true',help='Conserva contexto entre turnos')
    parser.add_argument('--dry-run',action='store_true',help='Interpreta sin ejecutar herramientas')
    args=parser.parse_args()
    dispatcher=Dispatcher(PersonalStore(args.db))
    def dispatch(text):
        print('INPUT:',text)
        result=dispatcher.dispatch_user_request(text,source='terminal',dry_run=args.dry_run)
        trace=dispatcher.semantic.trace
        for label,key in [('CONTEXT USED','context_used'),('INTERPRETER PROVIDER','interpreter_provider'),
                          ('PROVIDER ERROR','provider_error'),
                          ('SEMANTIC PLAN','semantic_plan'),('TOOLS SELECTED','tools_selected'),
                          ('EXECUTION','execution'),('CONTEXT UPDATED','context_updated')]:
            print(label+':',json.dumps(trace.get(key),ensure_ascii=False,default=str))
        print('VERIFICATION:',result.state)
        print('RESPONSE:',result.text)
    if args.text:dispatch(' '.join(args.text))
    if args.interactive:
        while True:
            try:text=input('KIRA> ').strip()
            except (EOFError,KeyboardInterrupt):break
            if text:dispatch(text)


if __name__=='__main__':main()
