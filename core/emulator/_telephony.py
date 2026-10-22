"""CallLog and SMS seed data generation and command formatting for Android emulator."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.emulator._text import shell_quote

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.emulator._seed_data import Contact


@dataclass(frozen=True, slots=True)
class CallLogSeedEntry:
    """A simulated call-log record to seed into Android telephony provider."""

    number: str
    timestamp_ms: int
    duration_sec: int
    call_type: int  # 1 = INCOMING, 2 = OUTGOING, 3 = MISSED


@dataclass(frozen=True, slots=True)
class SmsSeedEntry:
    """A simulated SMS message to seed into Android telephony provider."""

    sender: str
    body: str
    timestamp_ms: int
    read: int = 1


_SMS_TEMPLATES: dict[str, tuple[str, ...]] = {
    "en": (
        "Your mobile data package has been renewed.",
        "Package delivery scheduled for tomorrow between 10:00 and 14:00.",
        "Call me back when you have a moment.",
        "Payment receipt #48219 confirmed. Thank you.",
        "Reminder: appointment confirmed for 15:30.",
    ),
    "ru": (
        "Пакет мобильного интернета успешно продлен.",
        "Доставка посылки запланирована на завтра между 10:00 и 14:00.",
        "Перезвони мне, как будет время.",
        "Оплата чека #48219 подтверждена. Спасибо.",
        "Напоминание: запись подтверждена на 15:30.",
    ),
    "uk": (
        "Пакет мобільного інтернету успішно подовжено.",
        "Доставка посилки запланована на завтра з 10:00 до 14:00.",
        "Передзвони мені, коли буде час.",
        "Оплата чека #48219 підтверджена. Дякуємо.",
        "Нагадування: запис підтверджено на 15:30.",
    ),
    "de": (
        "Ihr Datenpaket wurde erfolgreich verlängert.",
        "Paketzustellung für morgen zwischen 10:00 und 14:00 Uhr geplant.",
        "Ruf mich zurück, wenn du Zeit hast.",
        "Zahlungsbestätigung #48219 erhalten. Vielen Dank.",
        "Erinnerung: Termin um 15:30 Uhr bestätigt.",
    ),
    "fr": (
        "Votre forfait internet mobile a été renouvelé avec succès.",
        "Livraison de votre colis prévue demain entre 10h00 et 14h00.",
        "Rappelle-moi quand tu auras un moment.",
        "Confirmation de paiement #48219 validée. Merci.",
        "Rappel: rendez-vous confirmé à 15h30.",
    ),
    "es": (
        "Su paquete de datos móviles ha sido renovado.",
        "Entrega del paquete programada para mañana entre 10:00 y 14:00.",
        "Llámame cuando tengas un momento.",
        "Pago confirmado recibo #48219. Gracias.",
        "Recordatorio: cita confirmada para las 15:30.",
    ),
    "it": (
        "Il tuo pacchetto dati mobili è stato rinnovato con successo.",
        "Consegna del pacco prevista per domani tra le 10:00 e le 14:00.",
        "Richiamami appena puoi.",
        "Pagamento confermato per la ricevuta #48219. Grazie.",
        "Promemoria: appuntamento confermato per le 15:30.",
    ),
    "nl": (
        "Uw mobiele databundel is succesvol verlengd.",
        "Pakketbezorging gepland voor morgen tussen 10:00 en 14:00.",
        "Bel me terug als je tijd hebt.",
        "Betaling bevestigd ontvangstbewijs #48219. Bedankt.",
        "Herinnering: afspraak bevestigd voor 15:30.",
    ),
    "pl": (
        "Twój pakiet internetu mobilnego został odnowiony.",
        "Dostawa paczki zaplanowana na jutro w godzinach 10:00 - 14:00.",
        "Oddzwoń do mnie w wolnej chwili.",
        "Potwierdzenie płatności #48219 zrealizowane. Dziękujemy.",
        "Przypomnienie: wizyta potwierdzona na 15:30.",
    ),
    "pt": (
        "Seu pacote de dados móveis foi renovado.",
        "Entrega de encomenda agendada para amanhã entre 10:00 e 14:00.",
        "Me liga de volta quando puder.",
        "Pagamento do recibo #48219 confirmado. Obrigado.",
        "Lembrete: agendamento confirmado para 15:30.",
    ),
    "tr": (
        "Mobil internet paketiniz basariyla yenilenmistir.",
        "Kargo teslimatiniz yarin 10:00 ile 14:00 arasinda planlanmistir.",
        "Musait oldugunda beni geri ara.",
        "Odeme makbuzu #48219 onaylandi. Tesekkur ederiz.",
        "Hatirlatma: Randevunuz saat 15:30 icin onaylanmistir.",
    ),
    "id": (
        "Paket data internet Anda telah berhasil diperpanjang.",
        "Pengiriman paket dijadwalkan besok antara pukul 10:00 dan 14:00.",
        "Telepon saya kembali jika ada waktu luang.",
        "Pembayaran tanda terima #48219 terkonfirmasi. Terima kasih.",
        "Pengingat: janji temu dikonfirmasi untuk pukul 15:30.",
    ),
}

_CALL_TYPE_INCOMING = 1
_CALL_TYPE_OUTGOING = 2
_CALL_TYPE_MISSED = 3
_CALL_TYPES = (
    _CALL_TYPE_INCOMING,
    _CALL_TYPE_INCOMING,
    _CALL_TYPE_OUTGOING,
    _CALL_TYPE_OUTGOING,
    _CALL_TYPE_MISSED,
)


def generate_call_and_sms_history(  # noqa: PLR0913
    phone_numbers: Sequence[str] | Sequence[Contact],
    *,
    seed: int | str,
    locale: str = "en_US",
    call_count: int = 6,
    sms_count: int = 4,
    now_ms: int | datetime | None = None,
) -> tuple[list[CallLogSeedEntry], list[SmsSeedEntry]]:
    """Deterministically generate realistic calls and SMS history localized to the locale."""
    rng = random.Random(seed)  # noqa: S311  # nosec B311 - fixture seeding, not crypto
    if isinstance(now_ms, datetime):
        base_ms = int(now_ms.timestamp() * 1000)
    elif now_ms is not None:
        base_ms = int(now_ms)
    elif seed is not None:
        base_ms = 1792627200000 + (rng.randint(0, 86400 * 14) * 1000)
    else:
        base_ms = int(datetime.now(UTC).timestamp() * 1000)
    raw_pool: list[str] = [str(getattr(c, "phone", c)) for c in phone_numbers]
    pool: list[str] = raw_pool or ["+12025550143", "+12025550188"]

    calls: list[CallLogSeedEntry] = []
    for i in range(call_count):
        num = rng.choice(pool)
        call_type = rng.choice(_CALL_TYPES)
        duration = 0 if call_type == _CALL_TYPE_MISSED else rng.randint(18, 420)
        age_ms = rng.randint(3_600_000 * (i + 1), 86_400_000 * 14)
        calls.append(
            CallLogSeedEntry(
                number=num,
                timestamp_ms=base_ms - age_ms,
                duration_sec=duration,
                call_type=call_type,
            )
        )

    lang = locale.replace("-", "_").split("_")[0].lower()
    templates = _SMS_TEMPLATES.get(lang, _SMS_TEMPLATES["en"])
    sms_list: list[SmsSeedEntry] = []
    for i in range(sms_count):
        sender = rng.choice(pool)
        body = templates[i % len(templates)]
        age_ms = rng.randint(7_200_000 * (i + 1), 86_400_000 * 21)
        sms_list.append(SmsSeedEntry(sender=sender, body=body, timestamp_ms=base_ms - age_ms))

    return calls, sms_list


def build_call_and_sms_seed_commands(
    calls: Sequence[CallLogSeedEntry],
    sms_list: Sequence[SmsSeedEntry],
) -> list[str]:
    """Build best-effort content insert commands for Android call log and SMS providers."""
    cmds: list[str] = [
        "appops set com.android.shell WRITE_SMS allow 2>/dev/null || true",
        "appops set com.android.shell READ_SMS allow 2>/dev/null || true",
        "appops set com.android.shell RECEIVE_SMS allow 2>/dev/null || true",
        "appops set com.android.shell WRITE_CALL_LOG allow 2>/dev/null || true",
        "appops set com.android.shell READ_CALL_LOG allow 2>/dev/null || true",
        "appops set com.android.shell WRITE_CONTACTS allow 2>/dev/null || true",
        "appops set com.android.shell READ_CONTACTS allow 2>/dev/null || true",
    ]
    if calls:
        call_dates = ",".join(str(c.timestamp_ms) for c in calls)
        cmds.append(
            f"content delete --uri content://call_log/calls "
            f"--where {shell_quote(f'date IN ({call_dates})')} 2>/dev/null || true"
        )
    if sms_list:
        sms_dates = ",".join(str(s.timestamp_ms) for s in sms_list)
        cmds.append(
            f"content delete --uri content://sms "
            f"--where {shell_quote(f'date IN ({sms_dates})')} 2>/dev/null || true"
        )
    cmds.extend(
        "content insert --uri content://call_log/calls "
        f"--bind number:s:{shell_quote(c.number)} "
        f"--bind date:l:{c.timestamp_ms} "
        f"--bind duration:i:{c.duration_sec} "
        f"--bind type:i:{c.call_type} "
        "--bind new:i:0 2>/dev/null || true"
        for c in calls
    )
    cmds.extend(
        "content insert --uri content://sms "
        f"--bind address:s:{shell_quote(s.sender)} "
        f"--bind body:s:{shell_quote(s.body)} "
        f"--bind date:l:{s.timestamp_ms} "
        f"--bind read:i:{s.read} "
        "--bind type:i:1 2>/dev/null || true"
        for s in sms_list
    )
    return cmds
