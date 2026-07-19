"""KL-85 Parte 3 — blocklist de domínios de e-mail descartáveis (temp-mail).

Usada SÓ na criação de conta (`POST /account/signup`) para reduzir contas-lixo/abuso.
**NÃO** afeta o scan anônimo (que funciona sem e-mail) nem os e-mails proativos.
Comparação por domínio exato, case-insensitive. Expandir a lista conforme necessário.
"""

from __future__ import annotations

DISPOSABLE_EMAIL_DOMAINS: frozenset = frozenset({
    # Maiores e mais conhecidos
    "mailinator.com", "mailinator.net", "mailinator2.com", "mailinater.com",
    "tempmail.com", "temp-mail.org", "tempail.com", "tempmailaddress.com",
    "tempinbox.com", "tempr.email", "tmpmail.net", "tmpmail.org", "tmail.com",
    "10minutemail.com", "zehnminutenmail.de", "mytemp.email",
    "guerrillamail.com", "guerrillamail.de", "guerrillamail.net", "guerrillamail.org",
    "guerrillamail.info", "guerrillamailblock.com", "grr.la", "sharklasers.com", "spam4.me",
    "throwaway.email", "throwam.com", "fakeinbox.com", "fakebox.net",
    "yopmail.com", "yopmail.fr", "trashmail.com", "trashmail.me", "trashmail.net",
    "trashmail.at", "trashmail.io", "trash-mail.com", "trashymail.com", "trashymail.net",
    "trashdevil.com", "trashdevil.de", "dispostable.com", "maildrop.cc", "mailnesia.com",
    "mohmal.com", "getnada.com", "nada.email", "nada.ltd", "emailondeck.com",
    "mailcatch.com", "meltmail.com", "mintemail.com", "mt2015.com", "pokemail.net",
    "spamgourmet.com", "harakirimail.com", "jetable.org", "jetable.com",
    "burnermail.io", "inboxkitten.com", "mailsac.com", "discard.email",
    "emailtemporario.com.br", "mailforspam.com", "mailexpire.com",
    "safetymail.info", "filzmail.com", "incognitomail.org", "mailnull.com",
    "antispam.de", "binkmail.com", "bobmail.info", "chammy.info", "devnullmail.com",
    "dodgeit.com", "dodgit.com", "dontreg.com", "e4ward.com", "emailigo.de",
    "emailmiser.com", "emailsensei.com", "imstations.com", "inboxed.im", "inboxed.pw",
    "insorg-mail.info", "ipoo.org", "kasmail.com", "koszmail.pl", "kurzepost.de",
    "lhsdv.com", "lroid.com", "maileater.com", "mailfreeonline.com", "mailme.ir",
    "mailme.lv", "mailmetrash.com", "mailmoat.com", "mailshell.com", "mailzilla.com",
    "mezimages.net", "mfsa.ru", "mmmmail.com", "mobi.web.id", "msgos.com", "mvrht.net",
    "mypartyclip.de", "mytrashmail.com", "nobulk.com", "nospamfor.us", "nowmymail.com",
    "objectmail.com", "obobbo.com", "onewaymail.com", "owlpic.com", "pjjkp.com",
    "plexfirm.com", "pookmail.com", "proxymail.eu", "putthisinyouremail.com",
    "receiveee.com", "regbypass.com", "rejectmail.com", "rhyta.com", "rklips.com",
    "rmqkr.net", "rppkn.com", "s0ny.net", "safe-mail.net", "safersignup.de",
    "safetypost.de", "saynotospams.com", "scatmail.com", "slaskpost.se", "slipry.net",
    "slopsbox.com", "smellfear.com", "snakemail.com", "sneakemail.com", "sofort-mail.de",
    "sogetthis.com", "soodonims.com", "spam.la", "spamavert.com", "spambob.net",
    "spambox.us", "spamcero.com", "spamday.com", "spamfree24.org", "spamgoes.in",
    "spamherelots.com", "spamhereplease.com", "spamhole.com", "spamify.com",
    "spaminator.de", "spamkill.info", "spaml.de", "spamoff.de", "spamslicer.com",
    "spamspot.com", "spamtrail.com", "superrito.com", "suremail.info", "teleworm.us",
    "thankyou2010.com", "thisisnotmyrealemail.com", "tittbit.in", "topranklist.de",
    "tradermail.info", "twinmail.de", "uggsrock.com", "upliftnow.com", "venompen.com",
    "viditag.com", "wegwerfmail.de", "wegwerfmail.net", "whyspam.me", "wilemail.com",
    "willselfdestruct.com", "wuzupmail.net", "xagloo.com", "xemaps.com", "xents.com",
    "xjoi.com", "xmaily.com", "xyzfree.net", "yogamaven.com", "yuurok.com",
    "zippymail.info", "zoaxe.com", "zoemail.org", "bugmenot.com",
})


def is_disposable_email(email: str) -> bool:
    """True se o domínio do e-mail está na blocklist de descartáveis."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain in DISPOSABLE_EMAIL_DOMAINS
