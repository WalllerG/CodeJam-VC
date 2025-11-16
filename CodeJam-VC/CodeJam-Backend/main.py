#! python3.7

import re
from datetime import datetime, timedelta


class Event:
    def __init__(self, activity=None, location=None,
                 start_year=None, start_month=None, start_day=None, start_hour=None, start_minute=None,
                 end_year=None, end_month=None, end_day=None, end_hour=None, end_minute=None):
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

    def get_start_time_str(self):
        """Format start time as string"""
        if self.start_hour is not None:
            # Convert back to 12-hour format for display
            display_hour = self.start_hour
            am_pm = "AM"
            if self.start_hour == 0:
                display_hour = 12
            elif self.start_hour == 12:
                am_pm = "PM"
            elif self.start_hour > 12:
                display_hour = self.start_hour - 12
                am_pm = "PM"

            time_str = f"{display_hour}"
            if self.start_minute is not None and self.start_minute > 0:
                time_str += f":{self.start_minute:02d}"
            time_str += f" {am_pm}"

            if self.start_year and self.start_month and self.start_day:
                time_str += f" {self.start_year}-{self.start_month:02d}-{self.start_day:02d}"
            return time_str
        return None

    def get_end_time_str(self):
        """Format end time as string"""
        if self.end_hour is not None:
            # Convert back to 12-hour format for display
            display_hour = self.end_hour
            am_pm = "AM"
            if self.end_hour == 0:
                display_hour = 12
            elif self.end_hour == 12:
                am_pm = "PM"
            elif self.end_hour > 12:
                display_hour = self.end_hour - 12
                am_pm = "PM"

            time_str = f"{display_hour}"
            if self.end_minute is not None and self.end_minute > 0:
                time_str += f":{self.end_minute:02d}"
            time_str += f" {am_pm}"

            if self.end_year and self.end_month and self.end_day:
                time_str += f" {self.end_year}-{self.end_month:02d}-{self.end_day:02d}"
            return time_str
        return None

    def to_dict(self):
        """Return all attributes as a dictionary"""
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
            "end_minute": self.end_minute
        }

    def is_valid(self):
        return self.activity is not None and self.activity.strip() != ""


def detect_time_period(sentence_lower, time_mention):
    """Detect if a time mention has morning/afternoon/evening context"""
    # Look for time period indicators near the time mention
    start_pos = max(0, time_mention['position'] - 30)  # Look 30 chars before
    end_pos = min(len(sentence_lower), time_mention['end_position'] + 30)  # Look 30 chars after

    context = sentence_lower[start_pos:end_pos]

    # Time period mappings
    period_indicators = {
        'morning': 'am',
        'afternoon': 'pm',
        'evening': 'pm',
        'night': 'pm',
        'tonight': 'pm',
        'this morning': 'am',
        'this afternoon': 'pm',
        'this evening': 'pm',
        'this night': 'pm'
    }

    for indicator, am_pm in period_indicators.items():
        if indicator in context:
            return am_pm

    return None


def parse_schedule_to_events(sentence, reference_date=None):
    """
    Parse natural language schedule and return list of Event objects
    """
    if reference_date is None:
        reference_date = datetime.now()

    sentence_lower = sentence.lower()
    events = []

    # Step 1: Extract date information FIRST (before time parsing)
    date_info = extract_detailed_date_info(sentence_lower, reference_date)

    # Step 2: Extract all time mentions and time ranges (but exclude relative days like "in 2 days")
    time_mentions = extract_all_time_mentions(sentence_lower)

    # Step 3: Extract locations (BEFORE activity extraction) - UPDATED to handle time periods
    location_mentions = extract_locations(sentence_lower, time_mentions)

    # Step 4: Extract activity (AFTER location extraction)
    activity = extract_clean_activity_full(sentence_lower, date_info, location_mentions, time_mentions)

    # Step 5: Handle relative time offsets like "in 1 hour", "in 30 minutes"
    events = handle_relative_times(sentence_lower, activity, location_mentions, date_info, reference_date,
                                   time_mentions)

    # If no relative times were found, proceed with regular time parsing
    if not events:
        events = create_events_with_datetime(sentence_lower, time_mentions, date_info, location_mentions, activity,
                                             reference_date)

    return events


def handle_relative_times(sentence_lower, activity, location_mentions, date_info, reference_date, time_mentions):
    """Handle relative time offsets like 'in 1 hour', 'in 30 minutes'"""
    events = []

    # Patterns for relative time offsets
    relative_time_patterns = [
        (r'in\s+(\d+)\s+hours?', 'hours'),
        (r'in\s+(\d+)\s+hour', 'hours'),  # singular
        (r'in\s+(\d+)\s+minutes?', 'minutes'),
        (r'in\s+(\d+)\s+minute', 'minutes'),  # singular
        (r'in\s+(\d+)\s+hrs?', 'hours'),
        (r'in\s+(\d+)\s+hr', 'hours'),  # singular
        (r'in\s+(\d+)\s+mins?', 'minutes'),
        (r'in\s+(\d+)\s+min', 'minutes'),  # singular
    ]

    found_relative_time = False

    for pattern, unit in relative_time_patterns:
        match = re.search(pattern, sentence_lower)
        if match and activity:
            amount = int(match.group(1))

            # Calculate the target time based on the relative offset
            if unit == 'hours':
                target_time = reference_date + timedelta(hours=amount)
            else:  # minutes
                target_time = reference_date + timedelta(minutes=amount)

            # Default duration of 1 hour for relative time events
            end_time = target_time + timedelta(hours=1)

            event = Event(
                activity=activity,
                start_year=target_time.year,
                start_month=target_time.month,
                start_day=target_time.day,
                start_hour=target_time.hour,
                start_minute=target_time.minute,
                end_year=end_time.year,
                end_month=end_time.month,
                end_day=end_time.day,
                end_hour=end_time.hour,
                end_minute=end_time.minute
            )

            if location_mentions:
                event.location = location_mentions[0]

            events.append(event)
            found_relative_time = True
            break

    return events


def extract_detailed_date_info(sentence_lower, reference_date):
    """Extract detailed date information including day-month combinations and relative dates"""
    date_info = {
        'year': reference_date.year,
        'month': reference_date.month,
        'day': reference_date.day
    }

    # Month mapping
    month_mapping = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    # Day of week mapping
    day_mapping = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }

    # Pattern 1: Relative dates like "in 2 days", "in 3 weeks"
    relative_patterns = [
        (r'in\s+(\d+)\s+days?', 'days'),
        (r'in\s+(\d+)\s+weeks?', 'weeks'),
        (r'in\s+(\d+)\s+months?', 'months'),
    ]

    found_relative_date = False
    for pattern, unit in relative_patterns:
        match = re.search(pattern, sentence_lower)
        if match:
            amount = int(match.group(1))
            if unit == 'days':
                target_date = reference_date + timedelta(days=amount)
            elif unit == 'weeks':
                target_date = reference_date + timedelta(weeks=amount)
            elif unit == 'months':
                # Approximate months as 30 days
                target_date = reference_date + timedelta(days=amount * 30)

            date_info.update({
                'year': target_date.year,
                'month': target_date.month,
                'day': target_date.day
            })
            found_relative_date = True
            break

    # Pattern 2: Specific date like "24 november" or "november 24" (only if no relative date found)
    if not found_relative_date:
        date_patterns = [
            r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)',
            r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})'
        ]

        found_specific_date = False

        for pattern in date_patterns:
            match = re.search(pattern, sentence_lower)
            if match:
                if match.lastindex >= 2:
                    if match.group(1).isdigit():
                        # Pattern: "24 november"
                        day = int(match.group(1))
                        month_name = match.group(2)
                    else:
                        # Pattern: "november 24"
                        month_name = match.group(1)
                        day = int(match.group(2))

                    month = month_mapping.get(month_name.lower())
                    if month and 1 <= day <= 31:
                        date_info['day'] = day
                        date_info['month'] = month
                        date_info['year'] = reference_date.year

                        # If the date has already passed this year, assume next year
                        try:
                            event_date = datetime(date_info['year'], date_info['month'], date_info['day'])
                            if event_date < reference_date:
                                date_info['year'] += 1
                        except ValueError:
                            pass  # Invalid date, keep current year

                        found_specific_date = True
                        break

    # Pattern 3: Relative dates and days of week (only if no specific or relative date found)
    if not found_relative_date and not found_specific_date:
        if 'tomorrow' in sentence_lower or 'tmrw' in sentence_lower:
            tomorrow = reference_date + timedelta(days=1)
            date_info.update({
                'year': tomorrow.year,
                'month': tomorrow.month,
                'day': tomorrow.day
            })
        elif 'today' in sentence_lower:
            # Already using reference_date (today)
            pass
        else:
            # Check for days of week
            for day_name, day_offset in day_mapping.items():
                if day_name in sentence_lower:
                    current_weekday = reference_date.weekday()
                    days_ahead = day_offset - current_weekday
                    if days_ahead <= 0:
                        days_ahead += 7  # Next week
                    target_date = reference_date + timedelta(days=days_ahead)
                    date_info.update({
                        'year': target_date.year,
                        'month': target_date.month,
                        'day': target_date.day
                    })
                    break

    return date_info


def extract_all_time_mentions(sentence_lower):
    """Extract all types of time mentions including decimal formats like 5.30, but exclude relative times"""
    time_mentions = []

    # Word-based times mapping
    word_times = {
        'twelve': '12', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'ten': '10', 'eleven': '11', 'noon': '12', 'midnight': '0'
    }

    # Complex time patterns - ordered by specificity (ranges first!)
    # Updated to handle decimal time formats like 5.30, 5.00
    time_patterns = [
        # Time ranges with AM/PM and decimal times: "from 5.30 to 6.30", "from 2.00 p.m. to 4.00 p.m."
        (r'(?:from|between)\s+(\d{1,2}(?:[.:]\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)?)\s+(?:to|until|till|-)\s+(\d{1,2}(?:[.:]\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)?)',
         'range_ampm'),

        # Time ranges without AM/PM with decimal times: "from 5.30 to 6.30", "from 2.00 to 4.00"
        (r'(?:from|between)\s+(\d{1,2}(?:[.:]\d{2})?)\s+(?:to|until|till|-)\s+(\d{1,2}(?:[.:]\d{2})?)', 'range'),

        # Full time with AM/PM and decimal times: "5.30 pm", "2.00 p.m.", "11.45 a.m."
        (r'(\d{1,2}[.:]\d{2}\s*(?:a\.m\.|p\.m\.|am|pm))', 'full_time_ampm'),
        (r'(\d{1,2}\s*(?:a\.m\.|p\.m\.|am|pm))', 'time_ampm'),

        # Decimal times without AM/PM: "5.30", "2.00", "11.45"
        (r'(\d{1,2}[.:]\d{2})', 'decimal_time'),

        # Simple times with "at" and decimal: "at 5.30", "at 2 pm", "at 2.00 p.m."
        (r'(?:at|by)\s+(\d{1,2}(?:[.:]\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)?)', 'at_time'),

        # O'clock times: "3 o'clock", "12 o'clock"
        (r'(\d{1,2})(?:\s*(?:o\'clock|oclock|clock))', 'oclock'),

        # Standalone times (only if no decimal found): "12", "3", "2"
        (r'(?<!\d)(\d{1,2})(?!\d|[.:])', 'standalone_time'),
    ]

    # Process each pattern
    for pattern, pattern_type in time_patterns:
        for match in re.finditer(pattern, sentence_lower):
            # Skip if this is part of a relative time pattern (e.g., "in 1 hour")
            context_before = sentence_lower[max(0, match.start() - 10):match.start()]
            if re.search(r'in\s+\d+\s*(?:hours?|minutes?|hrs?|mins?)$', context_before):
                continue

            # Skip if this match overlaps with a previously found time
            is_overlap = False
            for existing in time_mentions:
                if (match.start() < existing['end_position'] and
                        match.end() > existing['position']):
                    is_overlap = True
                    break

            if not is_overlap:
                time_data = {
                    'position': match.start(),
                    'end_position': match.end(),
                    'pattern': pattern_type,
                    'full_match': match.group(0)
                }

                # For ranges, store both start and end times
                if pattern_type.startswith('range') and match.lastindex >= 2:
                    time_data['time'] = match.group(1).strip()
                    time_data['end_time'] = match.group(2).strip()
                    time_data['type'] = 'range'
                else:
                    # For single times
                    time_data['time'] = match.group(1).strip()
                    time_data['type'] = 'single'

                time_mentions.append(time_data)

    # Add word-based times (but exclude numbers that are part of relative dates or times)
    for word, numeric in word_times.items():
        for match in re.finditer(r'\b' + word + r'\b', sentence_lower):
            # Skip if this is part of a relative date pattern
            if re.search(r'in\s+\d+\s+days?', sentence_lower[match.start() - 10:match.end() + 10]):
                continue

            # Skip if this is part of a relative time pattern
            if re.search(r'in\s+\d+\s*(?:hours?|minutes?|hrs?|mins?)$',
                         sentence_lower[match.start() - 10:match.start()]):
                continue

            # Skip if this overlaps with existing times
            is_overlap = False
            for existing in time_mentions:
                if (match.start() < existing['end_position'] and
                        match.end() > existing['position']):
                    is_overlap = True
                    break

            if not is_overlap:
                time_mentions.append({
                    'time': numeric,
                    'position': match.start(),
                    'end_position': match.end(),
                    'pattern': 'word_time',
                    'type': 'single',
                    'full_match': match.group(0)
                })

    # Sort by position and remove any remaining duplicates
    time_mentions.sort(key=lambda x: x['position'])

    # Remove duplicates (keep the first occurrence of each time at each position)
    unique_mentions = []
    seen_positions = set()

    for mention in time_mentions:
        pos_key = (mention['position'], mention.get('time', ''), mention.get('end_time', ''))
        if pos_key not in seen_positions:
            seen_positions.add(pos_key)
            unique_mentions.append(mention)

    return unique_mentions


def extract_locations(sentence_lower, time_mentions):
    """Extract locations with improved logic that distinguishes between time periods and locations"""
    location_indicators = ["at", "in", "on", "near", "around", "beside"]
    time_words = ['twelve', 'one', 'two', 'three', 'four', 'five', 'six',
                  'seven', 'eight', 'nine', 'ten', 'eleven', 'noon', 'midnight']

    # Time period words that should NOT be treated as locations
    time_period_words = ['morning', 'afternoon', 'evening', 'night', 'tonight']

    locations = []
    words = sentence_lower.split()

    for i, word in enumerate(words):
        if word in location_indicators and i + 1 < len(words):
            next_word = words[i + 1]

            # Skip if the next word is a time period word (e.g., "in the afternoon")
            if next_word in time_period_words:
                continue

            # Skip if the next word is "the" followed by a time period word (e.g., "in the morning")
            if (next_word == 'the' and i + 2 < len(words) and
                    words[i + 2] in time_period_words):
                continue

            # Skip if the next word is a time-related word or number (unless it's part of a location name)
            if (next_word in time_words or
                    (next_word.replace('.', '').isdigit() and not is_part_of_location(sentence_lower, i)) or
                    'am' in next_word or 'pm' in next_word or 'a.m.' in next_word or 'p.m.' in next_word):
                continue

            location_words = []
            j = i + 1

            # Collect location words until we hit another indicator or time word
            while (j < len(words) and
                   words[j] not in location_indicators + ['am', 'pm', 'a.m.', 'p.m.', 'to', 'from', 'until', 'and',
                                                          'then'] and
                   not (words[j].replace('.', '').isdigit() and not is_part_of_location(sentence_lower, j)) and
                   words[j] not in time_words and
                   words[j] not in time_period_words):  # Also stop at time period words
                location_words.append(words[j])
                j += 1

            if location_words:
                location = " ".join(location_words)
                # Clean up the location (remove trailing prepositions, etc.)
                location = re.sub(r'\s+(the|a|an|my|your|our)$', '', location)

                # Additional check: make sure this isn't actually a time period phrase
                if not any(period in location for period in time_period_words):
                    locations.append(location)

    return locations


def is_part_of_location(sentence_lower, word_index):
    """Check if a word at given index is part of a location name"""
    words = sentence_lower.split()
    if word_index >= len(words):
        return False

    # If the word is a number and is preceded by a location indicator, it's likely part of a location
    if word_index > 0 and words[word_index - 1] in ['at', 'in', 'on']:
        return True

    # If the word is a number and is part of a known location pattern (like "Room 101")
    if word_index > 0 and words[word_index - 1] in ['room', 'building', 'floor', 'apt', 'apartment']:
        return True

    return False


def parse_time_string(time_str, default_minute=0, context_am_pm=None):
    """Parse time string into hour and minute components with context-based AM/PM detection"""
    if not time_str:
        return None, default_minute

    hour = None
    minute = default_minute

    # Handle AM/PM with and without periods
    is_pm = ('p.m.' in time_str.lower() or 'pm' in time_str.lower() or
             context_am_pm == 'pm')
    is_am = ('a.m.' in time_str.lower() or 'am' in time_str.lower() or
             context_am_pm == 'am')

    # Clean the time string (remove AM/PM but keep the original for processing)
    time_str_clean = re.sub(r'\s*(a\.m\.|p\.m\.|am|pm)', '', time_str.lower()).strip()

    try:
        # Parse hour and minute - handle both colon and decimal formats
        if ':' in time_str_clean or '.' in time_str_clean:
            time_str_normalized = time_str_clean.replace('.', ':')
            parts = time_str_normalized.split(':')
            hour = int(parts[0])
            if len(parts) > 1 and parts[1]:
                minute = int(parts[1])
                if len(parts[1]) == 1:
                    minute *= 10
            else:
                minute = default_minute
        else:
            hour = int(time_str_clean)
            minute = default_minute

        # Adjust for AM/PM with context
        if is_pm and not is_am:
            # PM times: 12 PM stays 12, 1-11 PM become 13-23
            if hour == 12:
                hour = 12  # 12 PM = 12:00
            elif hour < 12:
                hour += 12  # 1-11 PM = 13-23
        elif is_am and not is_pm:
            # AM times: 12 AM becomes 0, 1-11 AM stay the same
            if hour == 12:
                hour = 0  # 12 AM = 00:00

    except (ValueError, TypeError):
        return None, default_minute

    return hour, minute


def create_events_with_datetime(sentence_lower, time_mentions, date_info, location_mentions, activity, reference_date):
    """Create events with detailed datetime attributes"""
    events = []

    if not time_mentions:
        # If no times found, create one event for the date with default times
        if activity:
            event = Event(
                activity=activity,
                start_year=date_info['year'],
                start_month=date_info['month'],
                start_day=date_info['day'],
                start_hour=9,
                start_minute=0,
                end_year=date_info['year'],
                end_month=date_info['month'],
                end_day=date_info['day'],
                end_hour=10,
                end_minute=0
            )
            if location_mentions:
                event.location = location_mentions[0]
            events.append(event)
        return events

    # Create events for each time mention
    for i, time_mention in enumerate(time_mentions):
        if activity:
            # Detect time period context for this time mention
            time_period = detect_time_period(sentence_lower, time_mention)

            if time_mention['type'] == 'range':
                # Detect time period for start and end times separately
                start_period = detect_time_period(sentence_lower, {
                    'position': time_mention['position'],
                    'end_position': time_mention['position'] + len(time_mention['time'])
                })
                end_period = detect_time_period(sentence_lower, {
                    'position': time_mention['end_position'] - len(time_mention['end_time']),
                    'end_position': time_mention['end_position']
                })

                start_hour, start_minute = parse_time_string(time_mention['time'], context_am_pm=start_period)
                end_hour, end_minute = parse_time_string(time_mention['end_time'], context_am_pm=end_period)
            else:
                # For single times, use the detected time period
                start_hour, start_minute = parse_time_string(time_mention['time'], context_am_pm=time_period)
                end_hour = (start_hour + 1) % 24 if start_hour is not None else None
                end_minute = start_minute

            if start_hour is not None and end_hour is not None:
                event = Event(
                    activity=activity,
                    start_year=date_info['year'],
                    start_month=date_info['month'],
                    start_day=date_info['day'],
                    start_hour=start_hour,
                    start_minute=start_minute,
                    end_year=date_info['year'],
                    end_month=date_info['month'],
                    end_day=date_info['day'],
                    end_hour=end_hour,
                    end_minute=end_minute
                )

                if i < len(location_mentions):
                    event.location = location_mentions[i]

                events.append(event)

    return events


def extract_clean_activity_full(sentence_lower, date_info, location_mentions, time_mentions):
    """Extract clean activity text by removing ALL non-activity components including locations and relative dates"""
    # Start with the original sentence
    cleaned = sentence_lower

    # Remove relative date patterns first
    relative_patterns = [
        r'in\s+\d+\s+days?',
        r'in\s+\d+\s+weeks?',
        r'in\s+\d+\s+months?',
    ]

    # Remove relative time patterns
    relative_time_patterns = [
        r'in\s+\d+\s+hours?',
        r'in\s+\d+\s+minutes?',
        r'in\s+\d+\s+hrs?',
        r'in\s+\d+\s+mins?',
    ]

    for pattern in relative_patterns + relative_time_patterns:
        cleaned = re.sub(pattern, '', cleaned)

    # Remove locations
    for location in location_mentions:
        # Remove the location from the sentence
        cleaned = re.sub(r'\b' + re.escape(location) + r'\b', '', cleaned)

    # Remove time mentions (including decimal times and AM/PM formats)
    for time_mention in time_mentions:
        cleaned = re.sub(re.escape(time_mention['full_match']), '', cleaned)

    # Days of week to remove
    days_of_week = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

    # Months to remove
    months = ['january', 'february', 'march', 'april', 'may', 'june',
              'july', 'august', 'september', 'october', 'november', 'december']

    # Relative dates to remove
    relative_dates = ['today', 'tomorrow', 'tmrw']

    # Time indicators to remove (including AM/PM formats)
    time_indicators = ['at', 'from', 'to', 'until', 'till', 'by', 'around', 'about', 'starting',
                       'am', 'pm', 'a.m.', 'p.m.']

    # Location indicators to remove
    location_indicators = ['at', 'in', 'on', 'near', 'around', 'beside']

    # Time period words to remove
    time_period_words = ['morning', 'afternoon', 'evening', 'night', 'tonight']

    # Common introductory phrases to remove
    intro_phrases = ['i have', 'i need', 'i want', "let's", 'we have', 'there is', "there's",
                     'schedule', 'plan', 'add', 'can you', 'please', 'could you', 'would you',
                     'i am', 'i will', 'i am going to', 'going to']

    # Articles and common words to remove
    common_words = ['a', 'an', 'the', 'my', 'your', 'our', 'their', 'some', 'any', 'am', 'will']

    # Remove introductory phrases first
    for phrase in intro_phrases:
        cleaned = re.sub(r'\b' + phrase + r'\b', '', cleaned)

    # Remove all the identified words
    all_words_to_remove = (days_of_week + months + relative_dates +
                           time_indicators + location_indicators + common_words +
                           time_period_words)

    for word in all_words_to_remove:
        cleaned = re.sub(r'\b' + word + r'\b', '', cleaned)

    # Clean up: remove punctuation and extra spaces
    cleaned = re.sub(r'[.,!?;]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Split and filter words - only keep meaningful activity words
    words = cleaned.split()
    filtered_words = []

    for word in words:
        # Skip single letters and numbers (including decimal numbers)
        if len(word) > 1 and not word.replace('.', '').isdigit():
            filtered_words.append(word)

    activity = " ".join(filtered_words)

    return activity if activity else "event"


def get_all_events_as_dicts(sentence, reference_date=None):
    """
    Main function to parse sentence and return all events as dictionaries
    """
    events = parse_schedule_to_events(sentence, reference_date)
    return [event.to_dict() for event in events]


def json_to_google_event(data):
    pad = lambda n: str(n).zfill(2)

    start_datetime = (
        f"{data['start_year']}-"
        f"{pad(data['start_month'])}-"
        f"{pad(data['start_day'])}T"
        f"{pad(data['start_hour'])}:"
        f"{pad(data['start_minute'])}:00"
    )

    end_datetime = (
        f"{data['end_year']}-"
        f"{pad(data['end_month'])}-"
        f"{pad(data['end_day'])}T"
        f"{pad(data['end_hour'])}:"
        f"{pad(data['end_minute'])}:00"
    )

    return {
        "summary": data["activity"],
        "location": data.get("location"),
        "start": {
            "dateTime": start_datetime,
            "timeZone": "America/Toronto",
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": "America/Toronto",
        }
    }
