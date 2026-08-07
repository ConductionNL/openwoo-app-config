#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/weblog.py — gunicorn access-logger die reveal-tokens onleesbaar maakt.
#
# `/reveal/<token>` draagt de credential in het pad, want de ontvanger heeft
# geen account en klikt alleen een link. Gunicorn logt elke request-regel
# integraal, dus zonder deze logger schrijft het portaal bij elke onthulling
# het token in de access-log — de sleutel tot een wachtwoord, in platte tekst,
# in een bestand dat doorgaans wordt verzameld en bewaard.
#
# Meestal is dat token op het moment van loggen al verbrand. Maar niet altijd:
# faalt het claimen halverwege (ticket-store onbereikbaar), dan blijft het
# ticket geldig terwijl het token wél in de log staat. Dan is leestoegang tot
# de logs genoeg om het wachtwoord op te halen.
#
# Gevonden tijdens de dry-run van 2026-08-07. Het ontwerp legde vast dat de
# wáárde nooit gelogd wordt; de sleutel tot die waarde stond er vervolgens bij
# elke aanroep in.
#
# Writes: read-only (alleen logformattering)
# Idempotent: n.v.t.
# Requires: gunicorn
#
# Usage:
#   gunicorn --logger-class weblog.RedactingLogger server:app
"""Access-logger die het token uit /reveal/<token> weglaat."""

import re

from gunicorn.glogging import Logger

# Alles ná /reveal/ tot het volgende pad-scheidingsteken of einde.
_REVEAL = re.compile(r"(/reveal/)[^/\s?]+")
_REDACTED = r"\1<token>"


def redact(value):
    """Vervang het token in een pad of request-regel door <token>."""
    if not isinstance(value, str):
        return value
    return _REVEAL.sub(_REDACTED, value)


class RedactingLogger(Logger):
    """Gunicorn-logger die reveal-tokens uit de access-log houdt.

    Grijpt in op `atoms()` in plaats van op het formaat, zodat het werkt
    ongeacht welke access-log-format-string er is ingesteld: elk atom dat het
    pad kan bevatten (`r` = request line, `U` = URL-pad) wordt geschoond.
    """

    def atoms(self, resp, req, environ, request_time):
        data = super().atoms(resp, req, environ, request_time)
        for key in ("r", "U"):
            if key in data:
                data[key] = redact(data[key])
        return data
