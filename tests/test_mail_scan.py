from datetime import datetime
from email import policy
from email.message import EmailMessage

from custom_components.parcel_tracker.carriers.base import BERLIN
from custom_components.parcel_tracker.mail import parse_mail
from custom_components.parcel_tracker.mail.apply import apply_update
from custom_components.parcel_tracker.mail.scan import (
    labelled_numbers,
    link_numbers,
    shop_name,
)
from custom_components.parcel_tracker.models import Parcel

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=BERLIN)


def _msg(
    body: str, from_: str = "Beispiel Shop <versand@beispiel-shop.de>", html: str | None = None,
    subject: str = "Deine Bestellung wurde versandt",
) -> EmailMessage:
    msg = EmailMessage(policy=policy.default)
    msg["From"] = from_
    msg["Subject"] = subject
    msg["Date"] = "Wed, 30 Sep 2026 08:00:00 +0000"
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg


def _found(msg: EmailMessage) -> list[tuple[str, str, str | None]]:
    return [(u.carrier, u.number, u.title) for u in parse_mail(msg).updates]


# ----- links -----


def test_link_dhl_piececode():
    link = "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode=123456789012"
    assert link_numbers(link) == [("dhl", "123456789012")]


def test_link_dpd_path_and_gls_check_digit():
    assert link_numbers("https://tracking.dpd.de/status/de_DE/parcel/01234567890123") == [
        ("dpd", "01234567890123")
    ]
    # (GLS's lookup knows the parcel by 11 digits: the check digit goes)
    assert link_numbers("https://gls-group.eu/DE/de/paketverfolgung?match=123456789012") == [
        ("gls", "12345678901")
    ]


def test_link_hermes_fragment_and_ups():
    link = "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#H1234567890123456789"
    assert link_numbers(link) == [("hermes", "H1234567890123456789")]
    assert link_numbers("https://www.ups.com/track?loc=de_DE&tracknum=1Z999AA10123456784") == [
        ("ups", "1Z999AA10123456784")
    ]


def test_link_hermes_einrichtungs_service():
    link = "https://www.myhes.de/sendungsverfolgung?sendungsnummer=12345678901234"
    assert link_numbers(link) == [("hermes", "12345678901234")]


def test_link_redirect_with_dhl_number():
    target = "https%3A%2F%2Fwww.dhl.de%2Fverfolgen.html%3Fpiececode%3D00340434123456789012"
    assert link_numbers(f"https://click.beispiel-shop.de/r?u={target}") == [
        ("dhl", "00340434123456789012")
    ]


def test_link_other_parameters_and_hosts_are_not_read():
    assert link_numbers("https://www.dhl.de/de/privatkunden.html?cid=123456789012") == []
    assert link_numbers("https://www.beispiel-shop.de/order?piececode=123456789012") == []
    assert link_numbers("mailto:info@dhl.de") == []


# ----- labelled numbers -----


def test_labelled_own_form_needs_no_carrier():
    assert labelled_numbers("Sendungsnummer: H1234567890123456789") == [
        ("hermes", "H1234567890123456789")
    ]


def test_labelled_14_digits_need_a_carrier():
    assert labelled_numbers("Trackingnummer: 12345678901234") == []
    assert labelled_numbers("Versand mit Hermes\nTrackingnummer: 12345678901234") == [
        ("hermes", "12345678901234")
    ]
    assert labelled_numbers("Versand mit DPD\nSendungsnummer lautet 12345678901234") == [
        ("dpd", "12345678901234")
    ]


def test_labelled_ambiguous_carriers_skip():
    text = "Wir versenden mit DPD oder Hermes.\nSendungsnummer: 12345678901234"
    assert labelled_numbers(text) == []


def test_labelled_short_numbers_only_in_links():
    assert labelled_numbers("DHL Sendungsnummer: 123456789012") == []


def test_labelled_with_spaces():
    assert labelled_numbers("DHL Sendungsnummer: 0034 0434 1234 5678 9012") == [
        ("dhl", "00340434123456789012")
    ]


# ----- whole mails -----


def test_shop_mail_with_dhl_link_creates_named_parcel():
    html = (
        '<p>Dein Paket ist unterwegs.</p><a href="https://www.dhl.de/de/privatkunden/'
        'pakete-empfangen/verfolgen.html?piececode=123456789012">Sendung verfolgen</a>'
    )
    msg = _msg("Dein Paket ist unterwegs.", html=html)
    assert _found(msg) == [("dhl", "123456789012", "Beispiel Shop")]
    parcels: dict[str, Parcel] = {}
    [update] = parse_mail(msg).updates
    change = apply_update(parcels, update, NOW)
    assert change.created
    assert parcels["123456789012"].name == "Beispiel Shop"


def test_shop_mail_names_existing_unnamed_parcel_only():
    msg = _msg("Versand mit Hermes. Sendungsnummer: 12345678901234")
    [update] = parse_mail(msg).updates
    unnamed = Parcel("12345678901234", "hermes", "mail", None, NOW, NOW)
    parcels = {unnamed.number: unnamed}
    assert apply_update(parcels, update, NOW) is not None
    assert unnamed.name == "Beispiel Shop"
    named = Parcel("12345678901234", "hermes", "mail", "Sofa", NOW, NOW, name_manual=True)
    parcels = {named.number: named}
    apply_update(parcels, update, NOW)
    assert named.name == "Sofa"


def test_hermes_einrichtungs_service_mail():
    msg = _msg(
        "Ihre Lieferung wird von Hermes Einrichtungs Service zugestellt.\n"
        "Sendungsnummer: 12345678901234",
        from_="Hermes Einrichtungs Service <noreply@hes.de>",
        subject="Ihre Lieferung",
    )
    # (a carrier names no parcel)
    assert _found(msg) == [("hermes", "12345678901234", None)]


def test_private_and_forwarded_senders_name_nothing():
    body = "Versand mit Hermes. Sendungsnummer: 12345678901234"
    assert _found(_msg(body, from_="Erika Musterfrau <erika@gmail.com>")) == [
        ("hermes", "12345678901234", None)
    ]
    forwarded = _msg(body, subject="WG: Deine Bestellung wurde versandt")
    assert _found(forwarded) == [("hermes", "12345678901234", None)]


def test_shop_name_rules():
    assert shop_name(_msg("x", from_="Beispiel Shop Kundenservice <info@beispiel-shop.de>")) == (
        "Beispiel Shop"
    )
    company = _msg("x", from_="Beispiel Handels GmbH <info@b.de>")
    assert shop_name(company) == "Beispiel Handels GmbH"
    # a display name that is not the domain's may be a person's: the domain names the shop
    assert shop_name(_msg("x", from_="Max Muster <info@moebel-beispiel.de>")) == (
        "moebel-beispiel.de"
    )
    assert shop_name(_msg("x", from_="Mein Laden <mailer@shopify.com>")) == "Mein Laden"
    assert shop_name(_msg("x", from_="<noreply@mail.moebel-beispiel.de>")) == "moebel-beispiel.de"


def test_known_carrier_mail_is_not_scanned():
    # A Packstation receipt of DHL links to the parcel somebody sent: no parcel that comes.
    html = '<a href="https://www.dhl.de/verfolgen.html?piececode=123456789012">x</a>'
    msg = _msg(
        "Einlieferungsbeleg", from_="DHL <noreply@dhl.de>", html=html,
        subject="Einlieferungsbeleg: Ihr Versand an der Packstation",
    )
    assert parse_mail(msg).updates == []


def test_newsletter_without_numbers_stays_unrecognised():
    html = '<a href="https://www.dhl.de/de/privatkunden.html">DHL</a>'
    assert parse_mail(_msg("Neue Angebote!", html=html)).updates == []
