"""Serialized text entry shared by desktop, Remote and terminal.

Services own persistence and verification. Providers propose interpretation only.
No Qt, audio, Live session or provider credentials are required for local work.
"""
import logging
import re
import threading
from core.intent_interpreter import Result, interpret
from core.intent_context import context_for
from core.personal_store import PersonalStore


class Dispatcher:
    def __init__(self, store=None, registry=None, on_event=None, provider=None, *, principal=None, session_id="desktop"):
        self.store = store or PersonalStore()
        import os
        from memory.user_memory import UserMemory
        # Desktop identity comes from the OS account. Remote shares this owner
        # only after the existing pairing/authentication boundary.
        self.memory = UserMemory(self.store, principal or f'local-os:{os.getuid()}', session_id)
        self.user_id, self.session_id = self.memory.scope
        self.registry = registry
        self.on_event = on_event or (lambda event: None)
        self.provider = provider
        self.lock = threading.RLock()
        self.log = logging.getLogger('kira.dispatch')
        from core.text_tasks import TextTasks
        self.tasks = TextTasks()
        from core.operational_context import OperationalContext
        from core.semantic import SemanticInterpreter
        self.context = OperationalContext()
        restored = self.memory.load_state()
        self.context.update(**restored)
        for key in ('last_file','last_folder','last_resource','last_created_resource',
                    'last_opened_resource','last_download','last_image'):
            if isinstance(restored.get(key), str):
                self.context.remember(key, restored[key])
        self.semantic = SemanticInterpreter(self.context)

    def dispatch_user_request(self, text, source='chat', context=None, dry_run=False):
        from core.operational_context import use_context
        from memory.user_memory import use_memory
        with self.lock, use_context(self.context), use_memory(self.memory):
            self.memory.invalidate_derived(self.context)
            self.context.update(user_id=self.user_id, session_id=self.session_id,
                                user_memories=self.memory.context(text))
            if dry_run:
                with self.lock:
                    return self._semantic(text, execute=False)
            try:
                self.memory.message('user', str(text))
                result = self._dispatch_request(text, source, context)
                self.memory.message('assistant', result.text)
                return result
            finally:
                self.memory.invalidate_derived(self.context)
                self.memory.save_state(self.context.snapshot())

    def _dispatch_request(self, text, source='chat', context=None):
        with self.lock:
            text = re.sub(r'\s+', ' ', str(text or '')).strip()
            self.log.info('[INPUT] source=%s chars=%d', source, len(text))
            ctx = context_for(self.store)
            if context:
                for key in ('page', 'selected', 'visible', 'filter'):
                    if key in context: setattr(ctx, key, context[key])
            self.context.update(current_page=ctx.page or source, current_selection=ctx.selected,
                                current_filter=ctx.filter)
            self.context.turn('user', text)
            self.semantic.trace = {'interpreter_provider':'deterministic','context_used':self.context.snapshot(),
                                   'tools_selected':[], 'execution':[]}
            try:
                # A full user goal stays intact; only the model creates steps.
                clauses = [text]
                answers = []
                for clause in clauses:
                    result = self._dispatch(clause, ctx)
                    answers.append(result)
                    if result.domain != 'tasks': self.tasks.pending = None
                    if result.domain not in ('wallet','work'): ctx.pending = None
                    self.log.info('[RESPONSE] domain=%s state=%s', result.domain, result.state)
                    self.on_event({'domain': result.domain, 'state': result.state})
                    self.context.turn('assistant', result.text)
                    if result.state=='pending' and ctx.pending is None and self.tasks.pending is None and not self.context.value('pending_operation') and not self.context.value('pending_clarification'):
                        self.context.update(pending_clarification={'question':result.text,'missing_field':'details'},last_user_goal=clause)
                    if result.domain in ('wallet','work'):
                        ctx.domain = result.domain
                        from core.wallet import Wallet
                        from core.work import Work
                        records = Wallet(self.store).movements() if result.domain=='wallet' else Work(self.store).events()
                        semantic_last = self.context.value('last_wallet_transaction' if result.domain=='wallet' else 'last_work_event')
                        if self.semantic.trace.get('interpreter_provider')!='deterministic' and self.semantic.trace.get('execution') and isinstance(semantic_last,dict):
                            ctx.last[result.domain]=semantic_last['id']
                        latest = next((r for r in records if r['id']==ctx.last.get(result.domain)),None)
                        if latest:
                            self.context.update(**{'last_wallet_transaction' if result.domain=='wallet' else 'last_work_event':latest})
                    if self.semantic.trace:
                        self.semantic.trace['context_updated'] = self.context.snapshot()
                        self.semantic.trace['response'] = result.text
                    if result.state in ('pending', 'failed'): break
                if len(answers) == 1: return answers[0]
                return Result(True, '\n'.join(r.text for r in answers), answers[-1].state, answers[-1].domain)
            except Exception as exc:
                self.log.exception('[SERVICE] failed')
                return Result(True, 'No pude completar la operación: ' + str(exc), 'failed')

    def _dispatch(self, text, ctx):
        self.log.info('[DISPATCH] local')
        # Follow-ups belong to the same semantic owner before optimizations.
        if self.context.value('pending_operation') or self.context.value('pending_clarification'):
            return self._semantic(text)
        self._ensure_registry()
        task = self.tasks.handle(text,self.registry)
        if task is not None: return task
        # High-confidence app launch shortcut. It is capability grounded and
        # never asks an LLM to perform an unambiguous local operation.
        launch = self._direct_app_launch(text)
        if launch is not None:
            return launch
        # WhatsApp language and follow-ups belong to the semantic owner. The
        # registered tool retains recipient resolution and exact-ID guards.
        # Legacy filesystem regexes interpret destinations as search sources.
        # Retain the compatibility tool, but let the semantic owner plan files.
        plan = interpret(text, ctx)
        if plan and plan.domain=='wallet' and plan.intent in ('CREATE','MARK_PAID'):
            from core.spanish_numbers import extract_money
            _, remainder, _ = extract_money(text)
            extra, _, _ = extract_money(remainder)
            if extra is not None:
                return self._semantic(text)
        # Existing deterministic personal services remain usable offline when
        # the semantic provider is unavailable.
        # The existing contextual CRUD interpreter handles corrections and
        # confirmations that refer to the immediately preceding record.
        if plan and (plan.confidence != 'HIGH' or plan.question):
            return self._semantic(text)
        self._ensure_registry()
        if self.registry.has('personal_hub'):
            evidence = self.registry.run_result('personal_hub', {'command':text},
                                               {'session_memory':{'store':self.store}})
            result = Result(evidence.get('handled',False),evidence['text'],evidence['state'],evidence.get('domain',''),
                            evidence.get('intent',''),evidence.get('confidence','HIGH'))
            self.log.info('[INTERPRETER] domain=%s intent=%s confidence=%s', result.domain, result.intent, result.confidence)
            self.log.info('[VERIFY] state=%s', result.state)
            if result.handled:
                self.semantic.trace['tools_selected'].append('personal_hub')
                self.semantic.trace['execution'].append(evidence)
                return result
        return self._semantic(text)

    def _direct_app_launch(self, text):
        match = re.fullmatch(r'(?:abre|abrir|inicia|lanza)\s+(?:la\s+|el\s+)?(.+?)\s*[.!]?$', text.strip(), re.I)
        if not match:
            return None
        requested = match.group(1).strip().casefold()
        aliases = {'whatsapp': 'whatsapp', 'chrome': 'Google Chrome',
                   'google chrome': 'Google Chrome', 'safari': 'Safari',
                   'spotify': 'Spotify', 'terminal': 'Terminal',
                   'finder': 'Finder', 'vscode': 'Visual Studio Code',
                   'visual studio code': 'Visual Studio Code'}
        app = aliases.get(requested)
        if app is None:
            return None
        if requested == 'whatsapp' and self.registry.has('whatsapp_web'):
            evidence = self.registry.run_result('whatsapp_web', {'action': 'open'},
                                                {'session_memory': {'store': self.store}})
            result = Result(True, evidence['text'], evidence['state'], 'whatsapp')
            self.semantic.trace['tools_selected'].append('whatsapp_web')
            self.semantic.trace['execution'].append(evidence)
            return result
        if not self.registry.has('open_app'):
            return Result(True, 'No hay una herramienta registrada para abrir aplicaciones.', 'failed', 'system')
        evidence = self.registry.run_result('open_app', {'app_name': app},
                                            {'session_memory': {'store': self.store}})
        state = evidence['state'] if evidence['state'] == 'failed' else 'executed'
        self.semantic.trace['tools_selected'].append('open_app')
        self.semantic.trace['execution'].append(evidence)
        return Result(True, evidence['text'], state, 'system')

    def _semantic(self, text, execute=True):
        self._ensure_registry()
        provider = self.provider
        if provider is None:
            from core.provider_manager import ProviderManager
            provider = self.provider = ProviderManager()
        return self.semantic.dispatch(text, self.registry, provider,
                                      {'session_memory': {'store': self.store}}, execute=execute)

    def _ensure_registry(self):
        if self.registry is None:
            from core.action_loader import discover_actions
            from pathlib import Path
            self.registry = discover_actions(Path(__file__).resolve().parents[1]/'actions', logger=self.log.debug)


_shared = {}
_shared_lock = threading.RLock()
def shared_dispatcher(store=None, registry=None, on_event=None, *, principal=None, session_id="desktop"):
    """One runtime instance per local database, including both HUB pages."""
    from pathlib import Path
    store = store or PersonalStore()
    key = (str(Path(store.path).resolve()), principal, session_id)
    with _shared_lock:
        if key not in _shared:
            _shared[key] = Dispatcher(store, registry=registry, on_event=on_event, principal=principal, session_id=session_id)
        dispatcher = _shared[key]
        if registry is not None: dispatcher.registry = registry
        if on_event is not None: dispatcher.on_event = on_event
        return dispatcher
