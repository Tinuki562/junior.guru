from collections import namedtuple
from datetime import datetime
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from juniorguru.lib.discord_club import ClubMemberID
from juniorguru.sync.meetups import (
    generate_channel_message_content,
    generate_scheduled_event,
    generate_thread_message_content,
    is_meetup_scheduled_event,
    parse_icalendar,
    parse_json_dl,
    parse_json_dl_location,
    parse_meetup_url,
    thread_name,
)


StubScheduledEvent = namedtuple("ScheduledEvent", ["name", "creator_id"], defaults=[123])


@pytest.fixture
def icalendar_content() -> str:
    return (Path(__file__).parent / "pyvo.ics").read_text()


@pytest.fixture
def icalendar_tentative_content() -> str:
    return (Path(__file__).parent / "pyvo_tentative.ics").read_text()


@pytest.fixture
def json_dl_content() -> str:
    return (Path(__file__).parent / "json_dl.html").read_text()


@pytest.fixture
def event() -> dict[str, Any]:
    return dict(location=('Rohanské nábř. 19, 186 00 Karlín', 'Praha'),
                location_raw='Applifting, Rohanské nábř. 19, Praha',
                name='Mini sraz juniorů na akci pythonistů',
                name_raw='ReactGirls & Applifting meetup',
                starts_at=datetime(2021, 6, 24, 18, 30),
                url='https://www.meetup.com/reactgirls/events/292684010/',
                emoji='⚛️')


def test_parse_json_dl_location():
    json_dl = (
        '{"@type":"Place","name":"Mews","address":{"@type":"PostalAddress",'
        '"addressLocality":"Praha-Praha 2","addressCountry":"Czech Republic",'
        '"streetAddress":"nám. I. P. Pavlova 5, Vinohrady"},"geo":{"@type":"GeoCoordinates",'
        '"latitude":50.07499694824219,"longitude":14.429851531982422}}'
    )
    json_dl_location = json.loads(json_dl)

    assert (
        parse_json_dl_location(json_dl_location)
        == "Mews, nám. I. P. Pavlova 5, Vinohrady, Praha-Praha 2, Czech Republic"
    )


def test_parse_icalendar(icalendar_content):
    events = parse_icalendar(icalendar_content)
    keys = set(itertools.chain.from_iterable(event.keys() for event in events))

    assert keys == {"name_raw", "starts_at", "location_raw", "url"}


def test_parse_icalendar_provides_timezone_aware_datetime(icalendar_content):
    events = parse_icalendar(icalendar_content)
    have_timezone = [event["starts_at"].tzinfo for event in events]

    assert all(have_timezone)


def test_parse_icalendar_skips_tentative(icalendar_tentative_content):
    events = parse_icalendar(icalendar_tentative_content)

    assert [event["url"] for event in events] == ["https://pyvo.cz/brno-pyvo/2011-04/"]


def test_parse_json_dl(json_dl_content):
    events = parse_json_dl(json_dl_content, "https://www.meetup.com/reactgirls/events/")
    keys = set(itertools.chain.from_iterable(event.keys() for event in events))

    assert keys == {"name_raw", "starts_at", "location_raw", "url"}


def test_parse_json_dl_provides_timezone_aware_datetime(json_dl_content):
    events = parse_json_dl(json_dl_content, "https://www.meetup.com/reactgirls/events/")
    have_timezone = [event["starts_at"].tzinfo for event in events]

    assert all(have_timezone)


@pytest.mark.parametrize("scheduled_event, expected", [
    (StubScheduledEvent("Pyvo Meetup"), False),
    (StubScheduledEvent("Pyvo Meetup", creator_id=ClubMemberID.BOT), False),
    (StubScheduledEvent("Praha: Mini sraz juniorů na akci pythonistů"), False),
    (StubScheduledEvent("Praha: Mini sraz juniorů na akci pythonistů", creator_id=ClubMemberID.BOT), True),
])
def test_is_meetup_scheduled_event(scheduled_event, expected):
    assert is_meetup_scheduled_event(scheduled_event) is expected


def test_parse_meetup_url():
    text = (
        '**Akce:** ReactGirls & Applifting meetup\n'
        '**Více info:** https://www.meetup.com/reactgirls/events/292684010/\n\n'
        'Chceš se poznat s lidmi z klubu i naživo? Běžně se potkáváme na srazech vybraných komunit. Utvořte skupinku, ničeho se nebojte, a vyražte!'
    )
    assert parse_meetup_url(text) == 'https://www.meetup.com/reactgirls/events/292684010/'


def test_generate_scheduled_event(event: dict):
    params = generate_scheduled_event(event)

    assert sorted(params.keys()) == ['description', 'end_time', 'location', 'name', 'start_time']
    assert params['name'] == 'Praha: Mini sraz juniorů na akci pythonistů'
    assert 'ReactGirls & Applifting meetup' in params['description']
    assert 'https://www.meetup.com/reactgirls/events/292684010/' in params['description']
    assert params['start_time'] == datetime(2021, 6, 24, 18, 30)
    assert params['end_time'] == datetime(2021, 6, 24, 21, 30)
    assert params['location'] == 'Applifting, Rohanské nábř. 19, Praha'


def test_generate_channel_message(event: dict):
    text = generate_channel_message_content(event)

    assert 'Praha' in text
    assert '24.6.' in text
    assert '⚛️' in text
    assert 'ReactGirls & Applifting meetup' in text
    assert 'https://www.meetup.com/reactgirls/events/292684010/' in text


def test_thread_name(event: dict):
    assert thread_name(event) == 'Praha, 24.6. – ReactGirls & Applifting meetup'


def test_generate_thread_message():
    text = generate_thread_message_content('https://discord.com/events/769966886598737931/1132525354439946310',
                                           ['@jarda', '@tajtrlik', '@alena'])

    assert 'https://discord.com/events/769966886598737931/1132525354439946310' in text
    assert 'potkáš' in text
    assert '@alena @jarda @tajtrlik' in text


def test_generate_thread_message_without_mentions():
    text = generate_thread_message_content('https://discord.com/events/769966886598737931/1132525354439946310')

    assert 'https://discord.com/events/769966886598737931/1132525354439946310' in text
    assert 'potkáš' not in text
    assert '@' not in text
