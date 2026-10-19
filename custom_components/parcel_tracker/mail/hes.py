"""Mails of the Hermes Einrichtungs Service (no Home Assistant imports).

Furniture and large appliances ("HES"). Its 21-digit numbers are tracked on its own page
(myhes.de) only: no carrier of ours can be asked, the mails alone tell the status. The
recipient's name and address are never read: only number, status, day, window and the
shipper ("Deine Sendung von: …").
"""

from __future__ import annotations

import re
from datetime import date
from email.message import EmailMessage

from ..models import ParcelStatus
from .base import (
    MailUpdate,
    at,
    body_text,
    company_name,
    html_body_text,
    names_carrier,
    sent_at,
    shop_of,
    shorten,
    subject,
)
from .scan import link_numbers, mail_links

HES_SENDER = "hermes.serviceteam@hes.de"
HES_DOMAINS = ("hes.de", "myhes.de")

# The number as the mails show it: "9999201227000 04400001" (13 + 8 digits).
_NUMBER = r"\d(?:\d| (?=\d))*\d"
_LABELLED = re.compile(rf"Sendungsnummer\s*:?\s*({_NUMBER})")
# "Auftragseingang deiner Sendung 9999201227000 04400001"
_SUBJECT = re.compile(rf"\bSendung\s+({_NUMBER})")
_FORM = re.compile(r"\d{18,24}")
_SHIPPER = re.compile(r"Deine Sendung von\s*:?\s*\n?\s*(?P<shipper>[^\n]+)")

# "Auftragseingang": the shop handed the order to HES, no day yet.
_RECEIVED = re.compile(r"\bauftragseingang\b|\bauftrag\b.*\bübernommen\b")
_DELIVERED = re.compile(r"\b(?:wurde|ist)\b.*\b(?:zugestellt|geliefert|ausgeliefert)\b")
_TODAY = re.compile(r"\bheute\b")
_DAY_WORDS = re.compile(r"\blieferter?min\b|\blieferdatum\b|\bzeitfenster\b|\bunterwegs\b")
_NOT = re.compile(r"\bnicht\b|\bverschoben\b|\babgesagt\b")
_DAY_LABEL = re.compile(r"Lieferter?min|Lieferdatum|Liefertag|am", re.IGNORECASE)
_FULL_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_WINDOW = re.compile(
    r"(?:zwischen\s+)?(\d{1,2}):(\d{2})\s*(?:Uhr\s*)?(?:und|bis|[–-])\s*(\d{1,2}):(\d{2})"
)


def _number(msg: EmailMessage, text: str) -> str | None:
    for link in mail_links(msg):
        for carrier, number in link_numbers(link):
            if carrier == "hes":
                return number
    for pattern, where in ((_LABELLED, text), (_SUBJECT, subject(msg))):
        if found := pattern.search(where):
            number = found.group(1).replace(" ", "")
            if _FORM.fullmatch(number):
                return number
    return None


def _status(subj: str) -> ParcelStatus | None:
    lower = subj.lower()
    if _NOT.search(lower):
        return None  # a day taken back or a failed attempt: the next mail tells more
    if _DELIVERED.search(lower):
        return ParcelStatus.DELIVERED
    if _TODAY.search(lower):
        return ParcelStatus.OUT_FOR_DELIVERY
    if _DAY_WORDS.search(lower):
        return ParcelStatus.IN_TRANSIT
    if _RECEIVED.search(lower):
        return ParcelStatus.PRE_TRANSIT
    return None


def _day(text: str) -> tuple[date, re.Match[str]] | None:
    """The first full date behind a word for the delivery day, with its match."""
    for label in _DAY_LABEL.finditer(text):
        segment = text[label.end() : label.end() + 80]
        if full := _FULL_DATE.search(segment):
            try:
                day = date(int(full.group(3)), int(full.group(2)), int(full.group(1)))
            except ValueError:
                continue
            return day, full
    return None


def parse_hes_mail(msg: EmailMessage) -> list[MailUpdate]:
    """A mail of the Hermes Einrichtungs Service: number, status, day, window and shipper.

    "Auftragseingang" (the shop handed the order over) sets "Angekündigt". A subject about
    the delivery day ("Liefertermin", "Zeitfenster") sets "Unterwegs" with the first full
    date behind it and a window ("zwischen 10:00 und 14:00 Uhr"), "heute" sets "In
    Zustellung", "wurde zugestellt/geliefert" sets "Zugestellt". A subject with "nicht"
    tells no status. The shipper names the parcel: a company name through the naming
    gate, else the line as it is (HES delivers for shops, not for private senders); never
    a carrier.
    """
    subj = subject(msg)
    text = body_text(msg)
    if not _SHIPPER.search(text) and not _LABELLED.search(text):
        text = html_body_text(msg) or text
    number = _number(msg, text)
    if number is None:
        return []
    sent = sent_at(msg)
    title = shop = None
    if shipper := _SHIPPER.search(text):
        line = " ".join(shipper.group("shipper").split())
        if line and not names_carrier(line):
            title = company_name(line) or shorten(line)
            shop = shop_of(line)
    status = _status(subj)
    update = MailUpdate(number, "hes", status, sent, title=title, shop=shop)
    if status is ParcelStatus.DELIVERED:
        update.delivered_at = sent
        return [update]
    day = sent.date() if status is ParcelStatus.OUT_FOR_DELIVERY else None
    window_text = text
    if status is ParcelStatus.IN_TRANSIT and (found := _day(text)):
        day, match = found
        window_text = text[match.end() : match.end() + 120]
    if day:
        update.eta_date = day
        if window := _WINDOW.search(window_text):
            hours = [int(window.group(i)) for i in range(1, 5)]
            if hours[0] < 24 and hours[2] < 24 and hours[1] < 60 and hours[3] < 60:
                update.eta_from = at(day, hours[0], hours[1])
                update.eta_to = at(day, hours[2], hours[3])
    return [update]
