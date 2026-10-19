from datetime import date, datetime
from email import policy
from email.message import EmailMessage

from custom_components.parcel_tracker.carriers.base import BERLIN
from custom_components.parcel_tracker.links import tracking_url
from custom_components.parcel_tracker.mail import known_sender_domain, parse_mail
from custom_components.parcel_tracker.mail.apply import apply_update
from custom_components.parcel_tracker.models import Parcel, ParcelStatus

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BERLIN)
NUMBER = "999920122700004400001"
SHOWN = "9999201227000 04400001"
FROM = "Hermes.Serviceteam@HES.de <hermes.serviceteam@hes.de>"
LINK = f"https://myhes.de/de/tracking/{NUMBER}?utm_source=cm&amp;utm_medium=email"

# The "Auftragseingang" mail as HES sends it (HTML only), the recipient's name left out.
RECEIVED_HTML = f"""<html><body><table><tr><td>Auftragseingang</td></tr>
<tr><td><p>Guten Tag Erika Musterfrau,</p>
<p>Dein Auftrag wurde vom Hermes Einrichtungs Service übernommen und wird
schnellstmöglich für dich eingeplant. Sobald wir alle Informationen haben und ein
konkreter Liefertermin feststeht, melden wir uns umgehend aktiv bei dir. Wir teilen dir
dann das genaue Lieferdatum und ein voraussichtliches Zeitfenster mit.</p>
<p>Vielen Dank für dein Verständnis</p>
<p><b>Deine Sendung von:</b><br>Beispiel Matratzen GmbH (Hammer)</p>
<p><b>Sendungsnummer:</b><br><a href="{LINK}">{SHOWN}</a></p>
<p>Viele Grüße</p></td></tr></table></body></html>"""


def _msg(subject: str, html: str, from_: str = FROM) -> EmailMessage:
    msg = EmailMessage(policy=policy.default)
    msg["From"] = from_
    msg["Subject"] = subject
    msg["Date"] = "Mon, 19 Oct 2026 07:39:00 +0200"
    msg.set_content(html, subtype="html")
    return msg


def _received() -> EmailMessage:
    return _msg(f"Auftragseingang deiner Sendung {SHOWN}", RECEIVED_HTML)


def test_received_mail():
    [u] = parse_mail(_received()).updates
    assert (u.number, u.carrier, u.status, u.title) == (
        NUMBER, "hes", ParcelStatus.PRE_TRANSIT, "Beispiel Matratzen GmbH",
    )
    assert u.eta_date is None


def test_hes_is_a_known_sender():
    assert known_sender_domain("hermes.serviceteam@hes.de") == "hes.de"


def test_number_from_the_text_without_a_link():
    html = RECEIVED_HTML.replace(f'<a href="{LINK}">{SHOWN}</a>', SHOWN)
    [u] = parse_mail(_msg(f"Auftragseingang deiner Sendung {SHOWN}", html)).updates
    assert u.number == NUMBER


def test_number_from_the_subject_alone():
    html = "<p>Dein Auftrag wurde übernommen.</p>"
    [u] = parse_mail(_msg(f"Auftragseingang deiner Sendung {SHOWN}", html)).updates
    assert (u.number, u.title) == (NUMBER, None)


def test_delivery_day_and_window():
    html = (
        "<p>Dein Liefertermin steht fest: Mittwoch, 21.10.2026 zwischen 10:00 und 14:00 Uhr."
        f"</p><p>Deine Sendung von:<br>Beispiel Matratzen GmbH</p><p>Sendungsnummer:<br>{SHOWN}</p>"
    )
    [u] = parse_mail(_msg(f"Dein Liefertermin für deine Sendung {SHOWN}", html)).updates
    assert u.status is ParcelStatus.IN_TRANSIT
    assert u.eta_date == date(2026, 10, 21)
    assert (u.eta_from.hour, u.eta_to.hour) == (10, 14)


def test_delivered_and_not_delivered():
    html = f"<p>Sendungsnummer: {SHOWN}</p>"
    [done] = parse_mail(_msg("Deine Sendung wurde zugestellt", html)).updates
    assert done.status is ParcelStatus.DELIVERED
    [failed] = parse_mail(_msg("Deine Sendung konnte nicht zugestellt werden", html)).updates
    assert failed.status is None


def test_parcel_is_mail_only_and_links_to_myhes():
    parcels: dict[str, Parcel] = {}
    [u] = parse_mail(_received()).updates
    change = apply_update(parcels, u, NOW)
    parcel = parcels[NUMBER]
    assert change.created
    assert (parcel.carrier, parcel.name, parcel.status) == (
        "hes", "Beispiel Matratzen GmbH", ParcelStatus.PRE_TRANSIT,
    )
    # Neither Hermes nor 17track knows the number: only the mails tell its status.
    assert parcel.poll_target is None
    assert parcel.track17_target is None
    assert tracking_url(parcel) == f"https://myhes.de/de/tracking/{NUMBER}"


def test_existing_unnamed_parcel_gets_the_shop():
    parcel = Parcel(NUMBER, "hes", "mail", None, NOW, NOW)
    parcels = {NUMBER: parcel}
    [u] = parse_mail(_received()).updates
    apply_update(parcels, u, NOW)
    assert parcel.name == "Beispiel Matratzen GmbH"
