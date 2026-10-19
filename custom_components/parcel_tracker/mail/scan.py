"""Tracking numbers in any mail: carrier links and labelled numbers (no Home Assistant imports).

For the mails no parser of a shop or carrier knows: a shop that ships without the carrier
ever writing, the Hermes Einrichtungs Service, a small shop's "Ihre Bestellung wurde
versandt". Only two things are read, both safe on their own:

- a link to a carrier's tracking page (its host says the carrier, its number must have
  the form that carrier gives), also behind a redirect that names the page in a
  parameter. A link is never followed.
- a number right behind a label ("Sendungsnummer: …", "Trackingnummer …"). One of a form
  only one carrier gives is taken as it is; 14 digits (DPD or Hermes) only if exactly one
  of them is named near the label or in the mail. Shorter numbers count in links only.

The sender names the parcel (``shop_name``): only a mail sent by a shop itself, never a
forwarded one, a carrier's or one from a mail provider's address (a private person).
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from urllib.parse import parse_qsl, unquote, urlsplit

from .base import (
    body_text,
    company_name,
    domain_of,
    html_body_text,
    is_forwarded,
    names_carrier,
    part_links,
    sender,
    shorten,
    subject,
)

# The hosts of the carriers' tracking pages (a sub-domain counts as its parent). The
# Hermes Einrichtungs Service (furniture and large appliances, "HES") is tracked by
# Hermes: its numbers are booked under Hermes.
_HOSTS = {
    "dhl.de": "dhl",
    "dhl.com": "dhl",
    "deutschepost.de": "dhl",
    "dpd.de": "dpd",
    "dpd.com": "dpd",
    "dpd.at": "dpd",
    "dpdgroup.com": "dpd",
    "gls-pakete.de": "gls",
    "gls-group.eu": "gls",
    "gls-group.com": "gls",
    "gls-rtt.com": "gls",
    "myhermes.de": "hermes",
    "hermesworld.com": "hermes",
    "hermes-europe.co.uk": "hermes",
    "hes.de": "hermes",
    "myhes.de": "hermes",
    "hermes-einrichtungsservice.de": "hermes",
    "hermes-einrichtungs-service.de": "hermes",
    "ups.com": "ups",
}
# The forms each carrier gives its numbers (after spaces and hyphens are dropped).
_FORMS = {
    "dhl": re.compile(r"00340\d{15}|JJD\d{12,22}|[A-Z]{2}\d{9}[A-Z]{2}|\d{12}|\d{10}"),
    "dpd": re.compile(r"\d{14}"),
    # (12 digits: the 11 of the parcel and a check digit, see ``_normal``)
    "gls": re.compile(r"\d{11,12}"),
    "hermes": re.compile(r"H\d{19}|\d{14}"),
    "ups": re.compile(r"1Z[0-9A-Z]{16}"),
}
# Forms only one carrier gives: a labelled number of such a form needs no carrier named.
_OWN_FORMS = (
    ("dhl", re.compile(r"00340\d{15}|JJD\d{12,22}")),
    ("hermes", re.compile(r"H\d{19}")),
    ("ups", re.compile(r"1Z[0-9A-Z]{16}")),
)
# The parameters a tracking page takes the number in. Any other parameter (a campaign,
# a language, a session) is never read: a 12-digit id there is no DHL number.
_KEYS = frozenset(
    {
        "piececode", "idc", "tracking-id", "trackingid", "tracking_id", "trackingnumber",
        "tracking_number", "trackingnumbers", "tracknum", "tracknums", "tracknumbers",
        "inquirynumber1", "awb", "parcelno", "parcelnr", "parcelnumber", "pknr", "query",
        "match", "matchcode", "trackid", "number", "nummer", "sendungsnummer",
        "shipmentnumber", "shipmentid", "sendungsid", "auftragsnummer", "q", "search",
        "id", "sid", "barcode",
    }
)
# Forms of a number that several carriers give and that a label may name: 14 digits (DPD,
# Hermes). 10 to 12 digits are never read from a text (an eBay item number, an order
# number look the same): only a link to a carrier's page names such a number.
_LABEL_FORMS = {
    "dpd": re.compile(r"\d{14}"),
    "hermes": re.compile(r"\d{14}"),
}
# Parameters that carry another link (a shop's or a newsletter tool's redirect).
_URL_VALUE = re.compile(r"https?://", re.IGNORECASE)
_SPLIT = re.compile(r"[/#&=?,;|]+")

_LABEL = re.compile(
    r"(?:Sendungs(?:verfolgungs)?nummer|Sendungs-?Nr\.?|Sendungs-?ID|Paketnummer|Paket-Nr\.?"
    r"|Paketverfolgungsnummer|Tracking[- ]?(?:Nummer|Number|ID|Code|Nr\.?)|Trackingnummer"
    r"|Tracking\s*#|Track(?:ing)?\s+No\.?|Sendungsverfolgung)"
    r"(?:[\s:#.\-–]|\b(?:lautet|ist|is)\b)*(?P<number>[A-Z0-9](?:[A-Z0-9\-]| (?=\d))*[A-Z0-9])",
    re.IGNORECASE,
)
_NEAR = 300
_CARRIER_WORDS = (
    ("dhl", re.compile(r"\bdhl\b|\bdeutsche\s+post\b", re.IGNORECASE)),
    ("dpd", re.compile(r"\bdpd\b", re.IGNORECASE)),
    ("gls", re.compile(r"\bgls\b", re.IGNORECASE)),
    ("hermes", re.compile(r"\bhermes\b|\beinrichtungs[\s-]?service\b", re.IGNORECASE)),
    ("ups", re.compile(r"\bups\b", re.IGNORECASE)),
)

# Addresses of mail providers: a private person writes from there, never a shop.
_MAIL_PROVIDERS = frozenset(
    {
        "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "gmx.com",
        "web.de", "t-online.de", "freenet.de", "arcor.de", "online.de", "outlook.com",
        "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "msn.com",
        "yahoo.com", "yahoo.de", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
        "aol.de", "posteo.de", "posteo.net", "mailbox.org", "protonmail.com", "proton.me",
        "pm.me", "privaterelay.appleid.com", "email.de", "mail.de", "vodafone.de",
    }
)
# Words a sender's display name carries besides the shop's ("Beispiel Shop Team").
_DISPLAY_NOISE = re.compile(
    r"\s*[-|–:]?\s*\b(?:no-?reply|kundenservice|customer\s+service|service|team"
    r"|versand(?:abteilung)?|shipping|info|newsletter)\b\s*",
    re.IGNORECASE,
)
# Services shops send their mails through: the domain is theirs, the display name the shop's.
_PLATFORMS = frozenset(
    {
        "shopify.com", "myshopify.com", "shopifyemail.com", "amazonses.com", "sendgrid.net",
        "mailchimp.com", "mcsv.net", "mandrillapp.com", "mailjet.com", "klaviyomail.com",
        "klaviyo.com", "sendinblue.com", "brevo.com", "hubspotemail.net", "mailgun.org",
        "jtlshop.de", "shopware.com", "wix.com", "wixsite.com", "squarespace.com",
        "etsy.com", "rapidmail.de", "cleverreach.com",
    }
)


def _carrier_of(host: str) -> str | None:
    host = host.lower().rstrip(".")
    while host:
        if host in _HOSTS:
            return _HOSTS[host]
        host = host.partition(".")[2]
    return None


def _normal(carrier: str, value: str) -> str | None:
    """The number in ``value`` if it has a form ``carrier`` gives, else None."""
    number = re.sub(r"[\s\-]", "", unquote(value)).upper()
    if not _FORMS[carrier].fullmatch(number):
        return None
    if carrier == "gls":
        return number[:11]  # the lookup knows the parcel without the check digit
    return number


def link_numbers(link: str, depth: int = 0) -> list[tuple[str, str]]:
    """(carrier, number) a link to a carrier's tracking page names; [] for any other link.

    A redirect that names the page in a parameter counts for that page (one level deep).
    """
    try:
        url = urlsplit(link.strip())
    except ValueError:
        return []
    if url.scheme not in ("http", "https"):
        return []
    params = parse_qsl(url.query, keep_blank_values=False)
    carrier = _carrier_of(url.hostname or "")
    if carrier is None:
        if depth:
            return []
        found: list[tuple[str, str]] = []
        for _, value in params:
            value = value if _URL_VALUE.match(value) else unquote(value)
            if _URL_VALUE.match(value):
                found += link_numbers(value, depth + 1)
        return found
    values = [value for key, value in params if key.lower() in _KEYS]
    # ("…/parcel/<number>", "…/track/<number>", "…/sendungsinformation#<number>")
    values += _SPLIT.split(url.path)
    values += _SPLIT.split(url.fragment)
    numbers = []
    for value in values:
        if value and (number := _normal(carrier, value)) and number not in numbers:
            numbers.append(number)
    return [(carrier, number) for number in numbers]


def mail_links(msg: EmailMessage) -> list[str]:
    """The links of every text part of a mail."""
    links: list[str] = []
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            links += part_links(part)
    return links


def _named(text: str) -> set[str]:
    return {carrier for carrier, word in _CARRIER_WORDS if word.search(text)}


def labelled_numbers(text: str) -> list[tuple[str, str]]:
    """(carrier, number) right behind a parcel-number label in ``text``.

    A form only one carrier gives counts as it is. 14 digits count if exactly one carrier
    that gives them is named near the label, or else in the whole text.
    """
    everywhere = _named(text)
    found: list[tuple[str, str]] = []
    for match in _LABEL.finditer(text):
        raw = match.group("number")
        # ("Sendungsnummer 1234 5678 9012 34" or the number and the next word's digits)
        for value in dict.fromkeys((raw, raw.split()[0])):
            number = re.sub(r"[\s\-]", "", value).upper()
            carrier = next((c for c, form in _OWN_FORMS if form.fullmatch(number)), None)
            if carrier is None:
                fits = {c for c, form in _LABEL_FORMS.items() if form.fullmatch(number)}
                start, end = max(match.start() - _NEAR, 0), match.end() + _NEAR
                near = fits & _named(text[start:end])
                near = near if len(near) == 1 else fits & everywhere
                carrier = next(iter(near)) if len(near) == 1 else None
            if carrier and (number := _normal(carrier, number)):
                if all(number != known for _, known in found):
                    found.append((carrier, number))
                break
    return found


def scan_numbers(msg: EmailMessage) -> list[tuple[str, str]]:
    """(carrier, number) of every tracking link and labelled number of a mail."""
    found: list[tuple[str, str]] = []
    for link in mail_links(msg):
        found += link_numbers(link)
    text = f"{subject(msg)}\n{body_text(msg)}"
    found += labelled_numbers(text)
    html = html_body_text(msg)
    if html and html not in text:
        found += labelled_numbers(html)
    result: list[tuple[str, str]] = []
    for carrier, number in found:
        if all(number != known for _, known in result):
            result.append((carrier, number))
    return result


def _site(domain: str) -> str:
    """The domain a shop is known by ("mail.beispiel-shop.de" -> "beispiel-shop.de")."""
    labels = domain.split(".")
    keep = 3 if len(labels) > 2 and labels[-2] in ("co", "com", "org") else 2
    return ".".join(labels[-keep:])


def shop_name(msg: EmailMessage) -> str | None:
    """The name of the shop that sent a mail, None if no shop sent it.

    Its display name as a company name (``company_name``), else the display name without
    the words around it ("Beispiel Shop Kundenservice" -> "Beispiel Shop") if it is the
    domain's name too (or the mail came through a service that sends for shops), else
    the domain it was sent from ("beispiel-shop.de"). None for a forwarded mail (the name
    would be the forwarding person's), a mail provider's address (a private person), a
    carrier and a sending service without a display name.
    """
    if is_forwarded(msg):
        return None
    address, display = sender(msg)
    domain = domain_of(address)
    if not domain or _site(domain) in _MAIL_PROVIDERS or _carrier_of(domain):
        return None
    site = _site(domain)
    display = " ".join(display.split()).strip(" \"'")
    if display and "@" not in display and not names_carrier(display):
        if name := company_name(display):
            return name
        name = _DISPLAY_NOISE.sub(" ", display).strip(" -|–:")
        # A display name that is not the domain's may be a person's: only through a
        # service that sends for shops does it count as it is.
        words = re.findall(r"[^\W\d_]{3,}", name.lower())
        own = re.sub(r"[^a-z0-9äöüß]", "", site.lower())
        if name and (site in _PLATFORMS or any(word in own for word in words)):
            return shorten(name)
    if site in _PLATFORMS or names_carrier(site.replace(".", " ")):
        return None
    return site
