#!/usr/bin/env python3
"""Inventory the real registry. Explicit calls only; never sends test messages."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.action_loader import discover_actions
from core.personal_store import PersonalStore
from core.semantic import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', help='Explicit direct invocation (may change local state)')
    parser.add_argument('--args', default='{}', help='JSON arguments matching the registry schema')
    parser.add_argument('--db', help='Alternative personal SQLite database')
    parser.add_argument('--smoke', action='store_true', help='Run isolated direct filesystem and SQLite checks')
    options = parser.parse_args()
    if options.smoke:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1] / 'tests'), pattern='test_tool_health.py')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return sys.exit(0 if result.wasSuccessful() else 1)
    registry = discover_actions(Path(__file__).resolve().parents[1] / 'actions', logger=lambda _: None)
    rows = []
    for record in registry._all_records:
        rows.append(dict(tool=record.name, module=record.file, registered=record.valid,
                         description=record.description, arguments=record.parameters,
                         structured_result=bool(record.structured_handler),
                         direct_call='NOT_TESTED', result_verified='NOT_TESTED',
                         context_update='NOT_TESTED', status='UNTESTED' if record.valid else 'LOAD_FAILED'))
    if options.tool:
        declarations = {d['name']: d for d in registry.get_tool_declarations()}
        if options.tool not in declarations:
            parser.error('Tool is not registered')
        arguments = json.loads(options.args)
        validate(arguments, declarations[options.tool]['parameters'])
        context = {'session_memory': {'store': PersonalStore(options.db)}} if options.db else {}
        result = registry.run_result(options.tool, arguments, context)
        for row in rows:
            if row['tool'] == options.tool:
                row.update(direct_call=True, result_verified=result['state'] == 'verified',
                           context_update=bool(result.get('context')),
                           status='HEALTHY' if result['state'] == 'verified' else result['state'].upper())
        print('EXECUTION:', json.dumps(result, ensure_ascii=False, default=str))
    print(json.dumps({'registered':len(registry.names()), 'discovered':len(rows), 'tools':rows}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
