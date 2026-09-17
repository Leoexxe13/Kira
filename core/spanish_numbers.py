"""Exact Spanish quantities. Identification as money belongs to the caller."""
import re
from decimal import Decimal
from core.wallet import normalized, cents

UNITS = dict(zip(('cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco veintiseis veintisiete veintiocho veintinueve').split(), range(30)))
UNITS.update(un=1, una=1)
TENS = dict(zip('treinta cuarenta cincuenta sesenta setenta ochenta noventa'.split(), range(30,100,10)))
HUNDREDS = dict(zip('cien doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos'.split(), range(100,1000,100)))
HUNDREDS['ciento'] = 100
WORDS = set(UNITS) | set(TENS) | set(HUNDREDS) | {'mil','millon','millones','y'}

def integer_words(tokens):
    if not tokens: raise ValueError('Falta la cantidad')
    if 'millones' in tokens or 'millon' in tokens:
        i = next(i for i,t in enumerate(tokens) if t in ('millon','millones'))
        return integer_words(tokens[:i])*1000000 + (integer_words(tokens[i+1:]) if tokens[i+1:] else 0)
    if 'mil' in tokens:
        if tokens.count('mil') != 1: raise ValueError('Cantidad no válida')
        i = tokens.index('mil')
        return (integer_words(tokens[:i]) if i else 1)*1000 + (integer_words(tokens[i+1:]) if tokens[i+1:] else 0)
    value = 0
    if tokens[0] in HUNDREDS:
        value = HUNDREDS[tokens[0]]; tokens = tokens[1:]
    if not tokens: return value
    if len(tokens)==1 and tokens[0] in UNITS: return value+UNITS[tokens[0]]
    if tokens[0] in TENS:
        if len(tokens)==1: return value+TENS[tokens[0]]
        if len(tokens)==3 and tokens[1]=='y' and 0<UNITS.get(tokens[2],0)<10:
            return value+TENS[tokens[0]]+UNITS[tokens[2]]
    raise ValueError('Cantidad no válida')

def extract_money(text):
    """Return (decimal string, remainder, approximate). Never rounds estimates."""
    text = normalized(text)
    approximate = bool(re.search(r'\b(como|aproximadamente|unos|unas|pico|algo|casi|alrededor)\b',text))
    text = re.sub(r'\brd\$\s*','',text).replace('rd$','')
    match = re.search(r'(?<!\w)\d[\d.,]*(?:k|\s+mil)?(?!\w)',text)
    if match:
        token = match[0]
        value = cents(str(Decimal(token[:-1])*1000)) if token.endswith('k') else cents(token)
    else:
        tokens = list(re.finditer(r'\w+',text))
        start = next((i for i,t in enumerate(tokens) if t[0] in WORDS-{'y'}),None)
        if start is None:return None,text,approximate
        end=start
        while end<len(tokens) and tokens[end][0] in WORDS:end+=1
        words=[t[0] for t in tokens[start:end]]
        if words and words[-1]=='y':words.pop();end-=1
        value=integer_words(words)*100
        match_span=(tokens[start].start(),tokens[end-1].end())
        class Span:
            def start(self):return match_span[0]
            def end(self):return match_span[1]
        match=Span()
    remainder=(text[:match.start()]+' '+text[match.end():]).strip()
    remainder=re.sub(r'\bpesos?\b','',remainder).strip()
    return f'{value//100}.{value%100:02d}',remainder,approximate
