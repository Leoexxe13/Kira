"""Deterministic personal commands shared by chat, HUB and the tool adapter."""
from dataclasses import dataclass
import re
import sqlite3
from core.personal_store import PersonalStore
from core.wallet import Wallet, money, cents, normalized
from core.work import Work

AMOUNT = r'(\d[\d.,]*(?:\s+mil)?)'


@dataclass(frozen=True)
class PersonalResult:
    handled: bool
    text: str = ''
    state: str = 'verified'
    domain: str = ''


def handle(text, store=None):
    raw = re.sub(r'^kira[,:]?\s*', '', str(text).strip(), flags=re.I).strip(' ¿?¡!.')
    low = normalized(raw)
    store = store or PersonalStore()
    wallet, work = Wallet(store), Work(store)
    domain = ''

    def reply(message, state='verified'):
        return PersonalResult(True, message, state, domain)

    try:
        # All amounts are stored in integer cents; only an explicit past-tense
        # payment can settle a planned movement.
        match = re.fullmatch(r'(?:cobré|cobre|me pagaron|registra ingreso de)\s+' + AMOUNT + r'(?:\s+(?:de|por)\s+(.+))?', raw, re.I)
        if match:
            domain = 'wallet'
            mid = wallet.record('income', match[1], match[2] or '')
            return reply(f'Ingreso registrado: {money(cents(match[1]))}. #{mid}')
        match = re.fullmatch(r'(voy a (?:pagar|gastar)|aparta|gasté|gaste|pagué|pague|ya pagué|ya pague)\s+(?:los\s+)?' + AMOUNT + r'(?:\s+(?:de|en|para)\s+(.+))?', raw, re.I)
        if match:
            domain = 'wallet'
            verb, amount, detail = normalized(match[1]), match[2], match[3] or ''
            if verb.startswith('voy'):
                mid = wallet.record('planned', amount, detail)
                label = 'Gasto planeado registrado'
            elif verb == 'aparta':
                mid = wallet.record('reserve', amount, detail)
                label = 'Apartado registrado'
            else:
                mid, _ = wallet.pay(amount, detail)
                label = 'Gasto real registrado'
            return reply(f'{label}: {money(cents(amount))}. #{mid}')
        match = re.fullmatch(r'dale\s+' + AMOUNT + r'\s+a\s+(.+)', raw, re.I)
        if match:
            domain = 'wallet'
            return reply(f'¿Ya lo entregaste o quieres apartarlo? Di «pagué {match[1]} para {match[2]}» o «aparta {match[1]} para {match[2]}».', 'pending')
        match = re.fullmatch(r'(?:paga|marca pagado) movimiento #?(\d+)', low)
        if match:
            domain = 'wallet'
            mid, changed = wallet.pay(movement_id=match[1])
            return reply(f'Movimiento #{mid} ' + ('marcado como pagado.' if changed else 'ya estaba pagado; no se duplicó.'))
        match = re.fullmatch(r'(?:cambia|cambiar) (?:la )?categoria (?:del )?movimiento #?(\d+) a (.+)', low)
        if match:
            domain = 'wallet'
            wallet.recategorize(match[1],match[2])
            return reply('Categoría actualizada.')
        if low in ('cuanto me queda','resumen financiero','mi billetera','resumen billetera','resumen billetera hoy','resumen billetera este mes'):
            domain = 'wallet'
            return reply(wallet.summary_text('today' if low.endswith('hoy') else 'month'))
        if low == 'cuanto tengo comprometido':
            domain = 'wallet'
            s = wallet.summary()
            return reply(f"Comprometido: {money(s['planned']+s['reserve'])}; incluye {money(s['reserve'])} apartados.")
        if low in ('en que he gastado mas este mes','en que gaste mas este mes'):
            domain = 'wallet'
            groups = wallet.summary()['categories']
            return reply('\n'.join(f'{k}: {money(v)}' for k,v in sorted(groups.items(), key=lambda kv:kv[1], reverse=True)) or 'No hay gastos reales este mes.')
        if low in ('que gastos me faltan por pagar','que gastos tengo pendientes','muestrame mis ultimos gastos','ultimos movimientos'):
            domain = 'wallet'
            rows = wallet.movements(pending=low.startswith('que gastos'))
            if 'gastos' in low and not low.startswith('que gastos'):
                rows = [r for r in rows if r['kind'] != 'income']
            labels = {'income':'Ingreso','planned':'Planeado','reserve':'Apartado','expense':'Gasto'}
            return reply('\n'.join(f"#{r['id']} {r['description'] or r['category']} · {money(r['amount'])} · {labels[r['kind']]} / {'pagado' if r['state']=='paid' else 'pendiente'}" for r in rows[:20]) or 'Sin movimientos registrados.')

        if low in ('activa modo trabajo','empieza mi turno','inicia turno','inicia mi turno'):
            domain = 'work'
            sid, created = work.start_shift()
            return reply(f'Turno #{sid} iniciado.' if created else f'El turno #{sid} ya está activo.')
        if low in ('turno activo','consulta mi turno','cual es mi turno'):
            domain = 'work'
            shift = work.active_shift()
            return reply(f"Turno #{shift['id']} desde {shift['started']}." if shift else 'No hay turno activo.')
        if low in ('cierra el turno y hazme un resumen','cierra turno','cierra el turno','termina mi turno','cierra mi turno y dame el resumen'):
            domain = 'work'
            return reply('Turno cerrado.\n' + work.close_shift())
        if low in ('que paso hoy','resumen de trabajo','resumen turno'):
            domain = 'work'
            return reply(work.summary())
        if low in ('que incidencias tengo','que pendientes quedan','pendientes de trabajo','incidencias de trabajo'):
            domain = 'work'
            rows = work.pending('incident' if 'incidencias' in low else None)
            return reply('\n'.join(f"#{r['id']} {r['description']}" for r in rows) or 'No hay pendientes abiertos.')
        match = re.fullmatch(r'(?:le )?entregu[eé] (?:la )?ficha ([\w-]+) a (.+)', raw, re.I)
        if match:
            domain = 'work'
            eid = work.event('assign',raw,person=match[2],resource=match[1],state='assigned')
            return reply(f'Ficha {match[1]} asignada a {match[2]}. Evento #{eid}.')
        match = re.fullmatch(r'(?:registra que )?(?:la )?([A-Z]+-?\d+) sali[oó](?: a las (\d{1,2}:\d{2}))? con (.+)', raw, re.I)
        if match:
            domain = 'work'
            eid = work.event('departure',raw,person=match[3],resource=match[1],hour=match[2],state='out')
            return reply(f'Salida de {match[1]} a las {match[2]} registrada. Evento #{eid}.')
        match = re.fullmatch(r'qui[eé]n tiene (?:la )?ficha ([\w-]+)', raw, re.I)
        if match:
            domain = 'work'
            _, description = work.describe_holder(match[1])
            return reply(description)
        match = re.fullmatch(r'(?:devolvieron|devuelve|volvi[oó]|registra devoluci[oó] de) (?:la )?ficha ([\w-]+)', raw, re.I)
        if not match:
            match = re.fullmatch(r'(?:la )?ficha ([\w-]+) (?:volvi[oó]|fue devuelta)', raw, re.I)
        if match:
            domain = 'work'
            work.event('return',raw,resource=match[1],state='available')
            return reply(f'Ficha {match[1]} devuelta; historial conservado.')
        match = re.fullmatch(r'(?:corrige|correcci[oó]n)[,:]? (?:la )?ficha ([\w-]+) la tiene (.+)', raw, re.I)
        if match:
            domain = 'work'
            if not work.holder(match[1]):return reply('No hay registro previo de esa ficha.', 'failed')
            work.event('correction',raw,person=match[2],resource=match[1],state='assigned')
            return reply('Tenedor corregido; historial conservado.')
        if low in ('anota una incidencia','registra una incidencia'):
            return PersonalResult(True,'¿Qué ocurrió?', 'pending', 'work')
        match = re.fullmatch(r'(?:registra |anota |crea )?(?:una )?(incidencia|pendiente de trabajo|nota de trabajo)\s*:?\s+(.+)', raw, re.I)
        if match:
            domain = 'work'
            kind = {'incidencia':'incident','pendiente de trabajo':'pending','nota de trabajo':'note'}[normalized(match[1])]
            eid = work.event(kind,match[2],state='open' if kind != 'note' else 'recorded')
            return reply(f'Registrado: #{eid}.')
        match = re.fullmatch(r'(?:completa|resuelve) (?:pendiente|incidencia) de trabajo #?(\d+)', low)
        if match:
            domain = 'work'
            work.complete(match[1])
            return reply('Pendiente completado.')
        # Unmatched requests belong to the semantic interpreter.
        return PersonalResult(False)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return reply(str(exc), 'pending' if 'varios compromisos' in str(exc) else 'failed')
