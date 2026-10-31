#! python3
"""
VoiceCal schedule parser.

Turns a transcribed sentence such as

    "Dinner with Alex tomorrow at 7 at the Keg, then gym for 2 hours"

into one Event per scheduled activity. The sentence is normalised (a.m./p.m.,
number words, "noon", "half past"...), split into clauses, and each clause is
parsed for a date, a time / time range / duration, a location and an activity.
Anything that was guessed is reported back as a warning so the user can fix it
before the event reaches Google Calendar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

MAX_EVENTS = 10
MAX_TEXT_LENGTH = 2000
DEFAULT_TIME_ZONE = "America/Toronto"
DEFAULT_START_TIME = time(9, 0)
DEFAULT_DURATION = timedelta(hours=1)
MAX_DURATION = timedelta(hours=24)

MONTHS = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
    'august': 8, 'aug': 8, 'september': 9, 'sept': 9, 'sep': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}
WEEKDAYS = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
    'friday': 4, 'saturday': 5, 'sunday': 6,
}
HOUR_WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
    'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
}

MONTH_RE = '|'.join(sorted(MONTHS, key=len, reverse=True))
WEEKDAY_RE = '|'.join(WEEKDAYS)
HOUR_WORD_RE = '|'.join(sorted(HOUR_WORDS, key=len, reverse=True))
HOUR_TOKEN = rf'\d{{1,2}}|{HOUR_WORD_RE}'
# Amounts used by durations and relative offsets: "2", "1.5", "an", "half an", "a couple of"
COUNT_RE = rf'\d+(?:\.\d+)?|a couple of|a few|half an?|{HOUR_WORD_RE}|an?'
TIME_TOKEN = r'\d{1,2}(?:[:.]\d{2})?(?:\s*[ap]m)?'

# Words that carry no meaning at the edges of an activity or location
EDGE_WORDS = {
    'and', 'then', 'also', 'at', 'in', 'on', 'from', 'to', 'for', 'by', 'around',
    'about', 'the', 'a', 'an', 'of', 'this', 'next', 'until', 'till', 'starting',
    'with', 'some', 'my', 'our', 'is', 'are', 'be', 'it', 'that', 'so', 'just',
    'like', 'before', 'after', 'between', 'or', 'but', 'we', 'i', 'me',
}
NOT_LOCATIONS = {
    'person', 'time', 'advance', 'total', 'general', 'mind', 'fact', 'case', 'row',
    'bit', 'while', 'minute', 'hour', 'meantime', 'least', 'once', 'all', 'most',
}


class Event:
    def __init__(self, activity=None, location=None,
                 start_year=None, start_month=None, start_day=None, start_hour=None, start_minute=None,
                 end_year=None, end_month=None, end_day=None, end_hour=None, end_minute=None,
                 time_inferred=False, warnings=None):
        self.activity = activity
        self.location = location
        self.start_year = start_year
        self.start_month = start_month
        self.start_day = start_day
        self.start_hour = start_hour
        self.start_minute = start_minute
        self.end_year = end_year
        self.end_month = end_month
        self.end_day = end_day
        self.end_hour = end_hour
        self.end_minute = end_minute
        self.time_inferred = time_inferred
        self.warnings = list(warnings or [])

    @classmethod
    def from_datetimes(cls, activity, start, end, location=None, time_inferred=False, warnings=None):
        return cls(activity=activity, location=location,
                   start_year=start.year, start_month=start.month, start_day=start.day,
                   start_hour=start.hour, start_minute=start.minute,
                   end_year=end.year, end_month=end.month, end_day=end.day,
                   end_hour=end.hour, end_minute=end.minute,
                   time_inferred=time_inferred, warnings=warnings)

    def __str__(self):
        parts = []
        if self.activity:
            parts.append(f"Activity: {self.activity}")
        if self.location:
            parts.append(f"Location: {self.location}")

        start_time = self.get_start_time_str()
        end_time = self.get_end_time_str()

        if start_time:
            parts.append(f"Start: {start_time}")
        if end_time:
            parts.append(f"End: {end_time}")

        return "Event(" + ", ".join(parts) + ")"

    def __repr__(self):
        return self.__str__()

    def start_datetime(self):
        return _to_datetime(self.start_year, self.start_month, self.start_day,
                            self.start_hour, self.start_minute)

    def end_datetime(self):
        return _to_datetime(self.end_year, self.end_month, self.end_day,
                            self.end_hour, self.end_minute)

    def get_start_time_str(self):
        """Format start time as string"""
        return _format_time(self.start_datetime())

    def get_end_time_str(self):
        """Format end time as string"""
        return _format_time(self.end_datetime())

    def to_dict(self):
        """Return all attributes as a dictionary"""
        start, end = self.start_datetime(), self.end_datetime()
        return {
            "activity": self.activity,
            "location": self.location,
            "start_year": self.start_year,
            "start_month": self.start_month,
            "start_day": self.start_day,
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "end_year": self.end_year,
            "end_month": self.end_month,
            "end_day": self.end_day,
            "end_hour": self.end_hour,
            "end_minute": self.end_minute,
            "start": start.isoformat(timespec="minutes") if start else None,
            "end": end.isoformat(timespec="minutes") if end else None,
            "time_inferred": self.time_inferred,
            "warnings": self.warnings,
        }

    def is_valid(self):
        start, end = self.start_datetime(), self.end_datetime()
        return (self.activity is not None and self.activity.strip() != ""
                and start is not None and end is not None and end > start)


def _to_datetime(year, month, day, hour, minute):
    if None in (year, month, day, hour):
        return None
    try:
        return datetime(year, month, day, hour, minute or 0)
    except ValueError:
        return None


def _format_time(dt):
    if dt is None:
        return None
    hour = dt.hour % 12 or 12
    minutes = f":{dt.minute:02d}" if dt.minute else ""
    return f"{hour}{minutes} {'PM' if dt.hour >= 12 else 'AM'} {dt:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Normalisation and clause splitting
# ---------------------------------------------------------------------------

def _hour_value(token):
    return HOUR_WORDS.get(token.lower(), None) or int(token)


def normalize_text(text):
    """Rewrite spoken time phrases into one predictable form ("7:30 pm", "12 pm"...)."""
    t = ' '.join(text.replace('’', "'").split())

    # "p.m." / "P.M" / "7PM" -> "pm"
    t = re.sub(r'\b([ap])\.\s?m\b\.?', lambda m: m.group(1).lower() + 'm', t, flags=re.I)
    t = re.sub(r'\b(\d{1,2}(?:[:.]\d{2})?)\s*([ap])m\b', lambda m: f"{m.group(1)} {m.group(2).lower()}m",
               t, flags=re.I)

    t = re.sub(r'\b(?:noon|midday)\b', '12 pm', t, flags=re.I)
    t = re.sub(r'\bmidnight\b', '12 am', t, flags=re.I)

    # "half past seven" -> "7:30", "quarter to 8" -> "7:45"
    t = re.sub(rf'\b(half|quarter) past ({HOUR_TOKEN})\b',
               lambda m: f"{_hour_value(m.group(2))}:{'30' if m.group(1).lower() == 'half' else '15'}",
               t, flags=re.I)
    t = re.sub(rf'\b(?:a )?quarter (?:to|till|of) ({HOUR_TOKEN})\b',
               lambda m: f"{(_hour_value(m.group(1)) - 2) % 12 + 1}:45", t, flags=re.I)

    # "seven thirty" -> "7:30", "three o'clock" -> "3:00"
    minute_words = {'thirty': '30', 'fifteen': '15', 'forty five': '45', 'forty-five': '45'}
    t = re.sub(rf"\b({HOUR_TOKEN})\s+(thirty|fifteen|forty[- ]five|o'?\s?clock)\b",
               lambda m: f"{_hour_value(m.group(1))}:{minute_words.get(m.group(2).lower(), '00')}",
               t, flags=re.I)
    t = re.sub(r"\b(\d{1,2})\s*o'?\s?clock\b", r'\1:00', t, flags=re.I)

    # Number words used as clock times: "at seven", "from two to four", "nine pm"
    t = re.sub(rf'\b(at|by|around|from|to|until|till|and|between)\s+({HOUR_WORD_RE})\b'
               r'(?!\s+(?:of|people|persons?|guests?|days?|weeks?|months?|years?|hours?|minutes?|times?))',
               lambda m: f"{m.group(1)} {HOUR_WORDS[m.group(2).lower()]}", t, flags=re.I)
    t = re.sub(rf'\b({HOUR_WORD_RE})\s+([ap]m)\b',
               lambda m: f"{HOUR_WORDS[m.group(1).lower()]} {m.group(2).lower()}", t, flags=re.I)

    # "between 2 and 4" -> "from 2 to 4" so the "and" isn't treated as a clause separator
    t = re.sub(rf'\bbetween\s+({TIME_TOKEN})\s+and\s+({TIME_TOKEN})\b', r'from \1 to \2', t, flags=re.I)

    # "an hour and a half" / "2 and a half hours" -> "1.5 hours" / "2.5 hours"
    t = re.sub(rf'\b(an?|{HOUR_WORD_RE}|\d+) hours? and a half\b',
               lambda m: f"{_count_value(m.group(1)) + 0.5:g} hours", t, flags=re.I)
    t = re.sub(rf'\b({HOUR_WORD_RE}|\d+) and a half hours?\b',
               lambda m: f"{_count_value(m.group(1)) + 0.5:g} hours", t, flags=re.I)

    # "add it to my calendar" is an instruction, not a location
    t = re.sub(r'\b(?:to|on|in|into|onto)\s+(?:my|the|our)\s+(?:google\s+)?(?:calendar|schedule|agenda)\b',
               ' ', t, flags=re.I)
    return ' '.join(t.split())


_HARD_SPLIT = re.compile(
    r'\s*(?:;|!|\?|(?<!\bdr)(?<!\bmr)(?<!\bmrs)(?<!\bms)(?<!\bst)(?<!\bjr)\.(?=\s|$)|\balso\b,?)\s*', re.I)
_THEN_SPLIT = re.compile(r'\s*,?\s*\b(?:and then|then|after that|afterwards|afterward)\b,?\s*', re.I)
_SOFT_SPLIT = re.compile(r'(\s*,\s*|\s+and\s+)', re.I)


def split_clauses(text, ref):
    """
    Split a sentence into (ClauseInfo, separator) pairs, one per event.

    "." ";" "also" always split. "then" splits and marks the clause as following
    the previous one. "," and "and" only split when both sides carry their own
    time/date, so "dinner with Alex and Sam at 7" stays one event.
    """
    clauses = []
    for hard_piece in _HARD_SPLIT.split(text):
        if not hard_piece.strip():
            continue
        for i, piece in enumerate(_THEN_SPLIT.split(hard_piece)):
            if not piece.strip():
                continue
            for j, info in enumerate(_soft_split(piece, ref)):
                separator = 'and' if j > 0 else ('then' if i > 0 else 'start')
                clauses.append((info, separator))
    return clauses


def _soft_split(piece, ref):
    tokens = _SOFT_SPLIT.split(piece)
    infos = []
    current = tokens[0]
    current_info = parse_clause(current, ref)
    for k in range(1, len(tokens), 2):
        separator, part = tokens[k], tokens[k + 1]
        part_info = parse_clause(part, ref)
        own_event = part_info.activity or (current_info.has_time and part_info.has_time)
        if current_info.has_marker and part_info.has_marker and own_event:
            infos.append(current_info)
            current, current_info = part, part_info
        else:
            current = current + separator + part
            current_info = parse_clause(current, ref)
    infos.append(current_info)
    return infos


# ---------------------------------------------------------------------------
# Clause parsing
# ---------------------------------------------------------------------------

@dataclass
class ClauseInfo:
    text: str
    date: date | None = None
    start: time | None = None
    end: time | None = None
    duration: timedelta | None = None
    relative_start: datetime | None = None
    activity: str | None = None
    location: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def has_time(self):
        return self.start is not None or self.relative_start is not None

    @property
    def has_marker(self):
        return self.has_time or self.date is not None or self.duration is not None


def parse_clause(text, ref):
    info = ClauseInfo(text=text.strip())
    work = info.text
    hint = _period_hint(work)

    work = _strip_recurrence(work, info)
    work = _extract_date(work, ref, info)
    work = _extract_relative_time(work, ref, info)
    work = _extract_duration(work, info)
    work = _extract_time(work, hint, info)
    if info.start is not None:
        info.relative_start = None  # an explicit clock time beats "in 2 hours"

    work = _blank_matches(_PERIOD_RE, work)
    work = _remove_fillers(work)
    work = _extract_location(work, info)
    activity = _clean_phrase(work)
    info.activity = (activity[0].upper() + activity[1:])[:120] if activity else None
    return info


def _blank(text, start, end):
    """Replace a span with spaces so the indices of everything else stay valid."""
    return text[:start] + ' ' * (end - start) + text[end:]


def _blank_matches(pattern, text):
    return pattern.sub(lambda m: ' ' * len(m.group(0)), text)


def _count_value(token):
    token = token.lower().strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', token):
        return float(token)
    if token.startswith('half'):
        return 0.5
    if token == 'a couple of':
        return 2
    if token == 'a few':
        return 3
    if token in ('a', 'an'):
        return 1
    return HOUR_WORDS.get(token, 1)


def _unit_delta(amount, unit):
    if unit.lower().startswith('h'):
        return timedelta(minutes=round(amount * 60))
    return timedelta(minutes=round(amount))


_PERIOD_RE = re.compile(
    r'\b(?:(?:in|during)\s+the\s+|this\s+|at\s+|tomorrow\s+)?(?:morning|afternoon|evening|night)\b', re.I)


def _period_hint(text):
    if re.search(r'\bmorning\b', text, re.I):
        return 'am'
    if re.search(r'\b(?:afternoon|evening|tonight|night)\b', text, re.I):
        return 'pm'
    return None


_RECURRENCE_RE = re.compile(
    r'\b(?:every|each)(?:\s+(?:day|week|month|morning|afternoon|evening|night))?\b|\b(?:daily|weekly|monthly)\b',
    re.I)
_RECURRENCE_WARNING = "Repeating events aren't supported yet, so only the next one was added."


def _strip_recurrence(work, info):
    if _RECURRENCE_RE.search(work):
        info.warnings.append(_RECURRENCE_WARNING)
        work = _blank_matches(_RECURRENCE_RE, work)
    return work


# --- dates ---

_MONTH_DAY_RE = re.compile(
    rf'\b(?:on\s+)?(?:the\s+)?(?P<month>{MONTH_RE})\.?\s+(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b'
    r'(?:,?\s+(?P<year>\d{4}))?', re.I)
_DAY_MONTH_RE = re.compile(
    rf'\b(?:on\s+)?(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>{MONTH_RE})\b\.?'
    r'(?:,?\s+(?P<year>\d{4}))?', re.I)
_ORDINAL_RE = re.compile(r'\b(?:on\s+)?(?:the\s+)?(?P<day>\d{1,2})(?:st|nd|rd|th)\b', re.I)
_WEEKDAY_DATE_RE = re.compile(
    rf'\b(?:on\s+)?(?:(?P<mod>next|this|coming)\s+)?(?P<day>{WEEKDAY_RE})(?P<plural>s)?\b', re.I)
_IN_DAYS_RE = re.compile(rf'\bin\s+(?P<n>{COUNT_RE})\s+(?P<unit>days?|weeks?|months?)\b', re.I)
_DAY_AFTER_TOMORROW_RE = re.compile(r'\b(?:the\s+)?day after (?:tomorrow|tmrw)\b', re.I)
_TOMORROW_RE = re.compile(r'\b(?:tomorrow|tmrw|tmr|tomorow)\b', re.I)
_TODAY_RE = re.compile(r'\b(?:today|tonight)\b', re.I)
_NEXT_WEEK_RE = re.compile(r'\bnext week\b', re.I)
_WEEKEND_RE = re.compile(r'\b(?:(?P<mod>this|next|on the|the)\s+)?weekend\b', re.I)


def _add_months(day, months):
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    for candidate in (day.day, 30, 29, 28):
        try:
            return date(year, month, min(day.day, candidate))
        except ValueError:
            continue
    return date(year, month, 28)


def _calendar_date(year, month, day, today, info, spoken):
    try:
        result = date(int(year) if year else today.year, month, day)
    except ValueError:
        info.warnings.append(f"“{spoken.strip()}” isn't a real date, so I ignored it.")
        return None
    if not year and result < today:
        try:
            result = result.replace(year=result.year + 1)
        except ValueError:  # Feb 29 with no leap year next year
            result = date(result.year + 1, 3, 1)
    return result


def _ordinal_date(day, today, info, spoken):
    for months_ahead in range(0, 3):
        month_start = _add_months(today.replace(day=1), months_ahead)
        try:
            candidate = month_start.replace(day=day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    info.warnings.append(f"“{spoken.strip()}” isn't a real date, so I ignored it.")
    return None


def _weekday_date(match, today):
    target = WEEKDAYS[match.group('day').lower()]
    modifier = (match.group('mod') or '').lower()
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0 and modifier != 'this':
        days_ahead = 7  # "Monday" said on a Monday means next week
    result = today + timedelta(days=days_ahead)
    same_week = result - timedelta(days=result.weekday()) == today - timedelta(days=today.weekday())
    if modifier == 'next' and same_week:
        result += timedelta(days=7)  # "next Friday" said on Monday means Friday of next week
    return result


def _relative_date(match, today):
    amount = _count_value(match.group('n'))
    unit = match.group('unit').lower()
    if unit.startswith('month'):
        return _add_months(today, max(1, round(amount)))
    days = amount * 7 if unit.startswith('week') else amount
    return today + timedelta(days=round(days))


def _weekend_date(match, today):
    result = today + timedelta(days=(5 - today.weekday()) % 7)
    if (match.group('mod') or '').lower() == 'next':
        result += timedelta(days=7)
    return result


def _extract_date(work, ref, info):
    today = ref.date()
    resolvers = (
        (_MONTH_DAY_RE, lambda m: _calendar_date(m.group('year'), MONTHS[m.group('month').lower()],
                                                 int(m.group('day')), today, info, m.group(0))),
        (_DAY_MONTH_RE, lambda m: _calendar_date(m.group('year'), MONTHS[m.group('month').lower()],
                                                 int(m.group('day')), today, info, m.group(0))),
        (_ORDINAL_RE, lambda m: _ordinal_date(int(m.group('day')), today, info, m.group(0))),
        (_WEEKDAY_DATE_RE, lambda m: _weekday_date(m, today)),
        (_IN_DAYS_RE, lambda m: _relative_date(m, today)),
        (_DAY_AFTER_TOMORROW_RE, lambda m: today + timedelta(days=2)),
        (_TOMORROW_RE, lambda m: today + timedelta(days=1)),
        (_TODAY_RE, lambda m: today),
        (_NEXT_WEEK_RE, lambda m: today + timedelta(days=7)),
        (_WEEKEND_RE, lambda m: _weekend_date(m, today)),
    )

    # Earlier patterns are more specific and win; every match is blanked either way.
    chosen = None
    for pattern, resolve in resolvers:
        for match in pattern.finditer(work):
            if pattern is _WEEKDAY_DATE_RE and match.group('plural') and _RECURRENCE_WARNING not in info.warnings:
                info.warnings.append(_RECURRENCE_WARNING)
            result = resolve(match)
            if chosen is None and result is not None:
                chosen = result
            work = _blank(work, *match.span())

    info.date = chosen
    return work


# --- times ---

_RELATIVE_TIME_RE = re.compile(rf'\bin\s+(?P<n>{COUNT_RE})\s*(?P<unit>hours?|hrs?|h|minutes?|mins?)\b', re.I)
_DURATION_RE = re.compile(
    rf'\bfor\s+(?:about\s+|around\s+|roughly\s+)?(?P<n>{COUNT_RE})\s*(?P<unit>hours?|hrs?|h|minutes?|mins?)\b',
    re.I)
_ADJ_DURATION_RE = re.compile(r'\b(?P<n>\d+(?:\.\d+)?)[- ](?P<unit>hour|hr|minute|min)\b(?:\s+long\b)?', re.I)

_T = r'(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?'
_RANGE_RE = re.compile(
    rf'(?:\b(from|at|starting(?:\s+at)?)\s+)?(?<![\w:.]){_T}\s*(?:-|–|—|\bto\b|\buntil\b|\btill\b|\bthrough\b)'
    rf'\s*{_T}(?![\w:])', re.I)
_NOT_A_TIME = r'(?!\s*(?:people|persons?|guests?|%|percent|dollars?|bucks|km|miles?|kids|of\b))'
_SINGLE_TIME_RES = (
    # "at 7", "around 7:30", "@ 9"
    re.compile(rf'(?:\b(?:at|by|around|about|before|after|from|starting(?:\s+at)?)\s+|@\s*)'
               rf'(?P<h>\d{{1,2}})(?:[:.](?P<mi>\d{{2}}))?\s*(?P<ap>am|pm)?(?![\w:]){_NOT_A_TIME}', re.I),
    # "7 pm", "7:30 am"
    re.compile(r'(?<![\w:.])(?P<h>\d{1,2})(?:[:.](?P<mi>\d{2}))?\s*(?P<ap>am|pm)\b', re.I),
    # "19:30"
    re.compile(r'(?<![\w:.])(?P<h>\d{1,2}):(?P<mi>\d{2})(?![\w:])', re.I),
)


def _extract_relative_time(work, ref, info):
    match = _RELATIVE_TIME_RE.search(work)
    if match:
        info.relative_start = ref + _unit_delta(_count_value(match.group('n')), match.group('unit'))
        work = _blank(work, *match.span())
    return work


def _extract_duration(work, info):
    for pattern in (_DURATION_RE, _ADJ_DURATION_RE):
        match = pattern.search(work)
        if not match:
            continue
        duration = _unit_delta(_count_value(match.group('n')), match.group('unit'))
        work = _blank(work, *match.span())
        if duration <= timedelta(0):
            continue
        if duration > MAX_DURATION:
            info.warnings.append("That's longer than a day, so I capped it at 24 hours.")
            duration = MAX_DURATION
        info.duration = duration
        break
    return work


def _valid_clock(hour, minute, meridiem):
    return 0 <= hour <= 23 and 0 <= minute <= 59 and not (meridiem and hour == 0)


def _to_24h(hour, meridiem):
    if hour > 12 or not meridiem:
        return hour
    if meridiem.lower() == 'am':
        return 0 if hour == 12 else hour
    return 12 if hour == 12 else hour + 12


def _guess_24h(hour, hint):
    """Hour without am/pm: use morning/evening context, else 1-7 means afternoon/evening."""
    if hour > 12 or hour == 0:
        return hour, False
    if hint:
        return _to_24h(hour, hint), False
    if 1 <= hour <= 7:
        return hour + 12, True
    return hour, False


def _clock_label(value):
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'PM' if value.hour >= 12 else 'AM'}"


def _resolve_range(h1, m1, ap1, h2, m2, ap2, hint):
    sh, sm, eh, em = int(h1), int(m1 or 0), int(h2), int(m2 or 0)
    if not (_valid_clock(sh, sm, ap1) and _valid_clock(eh, em, ap2)):
        return None
    assumed = False
    if ap1 and ap2:
        s, e = _to_24h(sh, ap1), _to_24h(eh, ap2)
    elif ap2:  # "2 to 4 pm"; "11 to 1 pm" means 11 am
        e = _to_24h(eh, ap2)
        s = _to_24h(sh, ap2)
        if s * 60 + sm > e * 60 + em:
            s = _to_24h(sh, 'am' if ap2.lower() == 'pm' else 'pm')
    elif ap1:  # "11 am to 1"
        s = _to_24h(sh, ap1)
        e = _to_24h(eh, ap1)
        if e * 60 + em <= s * 60 + sm:
            e = _to_24h(eh, 'pm' if ap1.lower() == 'am' else 'am')
    else:
        s, assumed_start = _guess_24h(sh, hint)
        e, assumed_end = _guess_24h(eh, hint)
        if e * 60 + em <= s * 60 + sm and e < 12 and eh <= 12:
            e += 12  # keep "7 to 9" / "11 to 1" in order
        assumed = assumed_start or assumed_end
    return time(s % 24, sm), time(e % 24, em), assumed


def _extract_time(work, hint, info):
    found = []
    for match in _RANGE_RE.finditer(work):
        prefix, h1, m1, ap1, h2, m2, ap2 = match.groups()
        if not (prefix or m1 or ap1 or m2 or ap2):
            continue  # "2 to 3 people" is not a time range
        found.append((match.start(), match.group(0), _resolve_range(h1, m1, ap1, h2, m2, ap2, hint)))
        work = _blank(work, *match.span())

    for pattern in _SINGLE_TIME_RES:
        for match in pattern.finditer(work):
            hour, minute = int(match.group('h')), int(match.group('mi') or 0)
            meridiem = match.groupdict().get('ap')
            resolved = None
            if _valid_clock(hour, minute, meridiem):
                if meridiem:
                    hour24, assumed = _to_24h(hour, meridiem), False
                else:
                    hour24, assumed = _guess_24h(hour, hint)
                resolved = (time(hour24, minute), None, assumed)
            found.append((match.start(), match.group(0), resolved))
            work = _blank(work, *match.span())

    found.sort(key=lambda item: item[0])
    chosen = None
    for _, spoken, resolved in found:
        if resolved is None:
            info.warnings.append(f"“{spoken.strip()}” isn't a valid time, so I ignored it.")
        elif chosen is None:
            chosen = resolved
        else:
            info.warnings.append(f"I only used the first time and ignored “{spoken.strip()}”.")

    if chosen:
        info.start, info.end, assumed = chosen
        if assumed:
            info.warnings.append(f"Assumed {_clock_label(info.start)} — say “AM” if you meant morning.")
    return work


# --- activity and location ---

_FILLER_RES = [re.compile(p, re.I) for p in (
    r"\b(?:hey|hi|okay|ok|um+|uh+|uhm|hmm|please|thanks|thank you)\b",
    r"\b(?:can|could|would|will) you\b",
    r"\bremind me(?: to| about| of)?\b",
    r"\b(?:i|we)(?:'ve| have)? (?:got|gotta)(?: to)?\b",
    r"\b(?:i|we) (?:have|had|need|want|would like|will|should|must|plan)(?: to)?\b",
    r"\b(?:i'm|i am|we're|we are) (?:going to|gonna|planning to|having|meeting)\b",
    r"\b(?:i'll|we'll|i'd like to|we'd like to)\b",
    r"\b(?:there's|there is|there will be|let's|let us)\b",
    r"\b(?:going to|gonna|have to|need to|got to|gotta)\b",
    r"^\s*(?:add|put|schedule|set up|create|plan|note)\b(?:\s+(?:in|me|an? event|an? reminder|an?)\b)*",
    r"\b(?:an? )?(?:event|reminder) (?:for|called|named|to)\b",
)]

_LOCATION_RE = re.compile(
    r"(?:\b(?:at|in|inside|near|outside)\s+|@\s*)"
    r"(?P<loc>[\w'&.-]+(?: (?!(?:with|for|and|then|about|to|because|so|but|or|after|before|while|regarding)\b)"
    r"[\w'&.-]+){0,5})", re.I)


def _remove_fillers(work):
    for pattern in _FILLER_RES:
        work = _blank_matches(pattern, work)
    return work


def _clean_phrase(text):
    text = re.sub(r"[^\w\s'&/+#.-]", ' ', text)
    words = [w.strip(".-'") for w in text.split()]
    words = [w for w in words if w]
    while words and words[0].lower() in EDGE_WORDS:
        words.pop(0)
    while words and words[-1].lower() in EDGE_WORDS:
        words.pop()
    if all(w.replace('.', '').isdigit() for w in words):
        return ''
    return ' '.join(words)


def _extract_location(work, info):
    for match in _LOCATION_RE.finditer(work):
        location = _clean_phrase(match.group('loc'))
        location = re.sub(r'^(?:the|a|an)\s+', '', location, flags=re.I)
        if not location or location.lower() in NOT_LOCATIONS or location.replace('.', '').isdigit():
            continue
        info.location = location[:100]
        return _blank(work, *match.span())
    return work


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_schedule(sentence, reference_date=None):
    """
    Parse a natural-language schedule.

    Returns (events, warnings): a list of Event objects plus sentence-level
    warnings. Per-event guesses are stored on each Event's ``warnings``.
    """
    ref = (reference_date or datetime.now()).replace(second=0, microsecond=0)
    warnings = []
    if not sentence or not sentence.strip():
        return [], ["I didn't catch anything — try saying it again."]
    if len(sentence) > MAX_TEXT_LENGTH:
        sentence = sentence[:MAX_TEXT_LENGTH]
        warnings.append("That was a lot! I only read the first part.")

    events = []
    seen = set()
    context_date = None
    previous = None
    for info, separator in split_clauses(normalize_text(sentence), ref):
        if not (info.has_marker or info.activity):
            continue
        event_warnings = list(info.warnings)

        activity, location = info.activity, info.location
        if not activity:
            if previous is not None:
                activity = previous.activity
                location = location or previous.location
            else:
                activity = "New event"
                event_warnings.append("I couldn't tell what this event is — give it a name.")

        if info.date is not None:
            context_date = info.date
        day = context_date
        time_inferred = False

        if info.relative_start is not None:
            start = info.relative_start
        elif info.start is not None:
            start = datetime.combine(day or ref.date(), info.start)
            if day is None and start < ref:
                start += timedelta(days=1)
                event_warnings.append("That time already passed today, so it's set for tomorrow.")
        elif separator == 'then' and previous is not None and info.date is None:
            start = previous.end_datetime()
            event_warnings.append("Starts right after the previous event.")
        else:
            start = datetime.combine(day or ref.date(), DEFAULT_START_TIME)
            if day is None and start < ref:
                start += timedelta(days=1)
            time_inferred = True
            event_warnings.append("No time mentioned, so I picked 9:00 AM.")

        if info.end is not None and info.relative_start is None:
            end = datetime.combine(start.date(), info.end)
            if end <= start:
                end += timedelta(days=1)  # "10 pm to 2 am" ends the next day
        else:
            end = start + (info.duration or DEFAULT_DURATION)

        if day is not None and start < ref:
            event_warnings.append("This is in the past — double-check the date.")

        key = (activity.lower(), start)
        if key in seen:
            continue
        seen.add(key)

        event = Event.from_datetimes(activity, start, end, location=location,
                                     time_inferred=time_inferred, warnings=event_warnings)
        events.append(event)
        previous = event

        if len(events) == MAX_EVENTS:
            warnings.append(f"I can add up to {MAX_EVENTS} events at once, so I stopped there.")
            break

    if not events:
        warnings.append("I couldn't find an event in that. Try something like “Lunch with Sam tomorrow at noon”.")
    return events, warnings


def parse_schedule_to_events(sentence, reference_date=None):
    """
    Parse natural language schedule and return list of Event objects
    """
    return parse_schedule(sentence, reference_date)[0]


def get_all_events_as_dicts(sentence, reference_date=None):
    """
    Main function to parse sentence and return all events as dictionaries
    """
    events = parse_schedule_to_events(sentence, reference_date)
    return [event.to_dict() for event in events]


def _datetime_from(data, prefix):
    value = data.get(prefix)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return datetime(data[f'{prefix}_year'], data[f'{prefix}_month'], data[f'{prefix}_day'],
                    data[f'{prefix}_hour'], data.get(f'{prefix}_minute') or 0)


def json_to_google_event(data, time_zone=DEFAULT_TIME_ZONE):
    """Convert an event dict (ISO "start"/"end" or the split year/month/... fields) to a Google Calendar body."""
    start = _datetime_from(data, 'start').replace(tzinfo=None, second=0, microsecond=0)
    end = _datetime_from(data, 'end').replace(tzinfo=None, second=0, microsecond=0)
    if end <= start:
        raise ValueError("The event has to end after it starts.")

    summary = (data.get("activity") or "").strip()
    if not summary:
        raise ValueError("The event needs a name.")

    body = {
        "summary": summary[:200],
        "description": "Added with VoiceCal",
        "start": {"dateTime": start.isoformat(), "timeZone": time_zone},
        "end": {"dateTime": end.isoformat(), "timeZone": time_zone},
    }
    location = (data.get("location") or "").strip()
    if location:
        body["location"] = location[:200]
    return body
