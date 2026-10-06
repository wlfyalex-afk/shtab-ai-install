"""Dependency-free validation for secretary call contacts."""
import re
from urllib.parse import quote

KINDS = {'EXTENSION':'Добавочный', 'MOBILE':'Мобильный', 'LANDLINE':'Городской', 'SIP_URI':'SIP-адрес'}

def normalize_contact(kind, value):
    value = (value or '').strip()
    if not value or len(value) > 200:
        raise ValueError('Введите телефон или SIP-адрес длиной до 200 символов.')
    if kind == 'EXTENSION':
        if not re.fullmatch(r'[0-9]{1,10}', value):
            raise ValueError('Добавочный номер должен содержать от 1 до 10 цифр.')
        return value
    if kind in ('MOBILE','LANDLINE'):
        if not re.fullmatch(r'\+?[0-9 ()-]{5,40}', value):
            raise ValueError('Телефон содержит недопустимые символы.')
        digits = re.sub(r'\D', '', value)
        if not 5 <= len(digits) <= 15:
            raise ValueError('Телефон должен содержать от 5 до 15 цифр.')
        return ('+' if value.startswith('+') else '') + digits
    if kind == 'SIP_URI':
        candidate = value if value.lower().startswith('sip:') else 'sip:' + value
        if not re.fullmatch(r'sip:[A-Za-z0-9_.+%-]+(?:@[A-Za-z0-9.-]+(?::[0-9]{1,5})?)?', candidate):
            raise ValueError('SIP-адрес должен иметь вид sip:215 или sip:user@pbx.example.')
        return 'sip:' + candidate[4:]
    raise ValueError('Неизвестный тип контакта.')

def dial_uri(kind, normalized):
    target = normalized[4:] if kind == 'SIP_URI' and normalized.lower().startswith('sip:') else normalized
    return 'sip:' + quote(target, safe='@:+_.%-')
