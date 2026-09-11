"""Konštanty integrácie Magna iPortál."""

from __future__ import annotations

DOMAIN = "magna"

BASE_URL = "https://iportal.magna-energia.sk"

# Portál bez hlavičiek prehliadača vracia HTTP 466 (overené 10. 9. 2026).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Dáta sa menia raz denne (portál hlási "aktuálne údaje za včerajší deň")
# a priebehy požičovne až pri fakturácii. Častejšie sťahovanie nemá zmysel.
UPDATE_INTERVAL_HOURS = 12

# interval v load.php
INTERVAL_DAY = 1
INTERVAL_WEEK = 2
INTERVAL_MONTH = 3

# granularity v load.php
GRANULARITY_15MIN = 1
GRANULARITY_HOUR = 2
GRANULARITY_DAY = 3

# Koľko mesiacov dozadu sa naimportuje pri prvom spustení. Potom už len
# aktuálny a predchádzajúci mesiac -- priebehy požičovne pribúdajú spätne,
# takže predchádzajúci mesiac treba prepisovať.
HISTORY_MONTHS_FIRST_RUN = 12
HISTORY_MONTHS_REFRESH = 2

# Druh odberného miesta sa rozpozná zo sufixu labelu. JS v portáli robí to
# isté (mg_get_data() testuje koniec na "- Požičovňa").
KIND_CONSUMPTION = "spotreba"
KIND_BANK_RETURN = "pozicovna"
KIND_SURPLUS = "prebytok"

SUFFIX_TO_KIND = {
    "- požičovňa": KIND_BANK_RETURN,
    "- prebytok výroby": KIND_SURPLUS,
}

KIND_NAMES = {
    KIND_CONSUMPTION: "Spotreba",
    KIND_BANK_RETURN: "Požičovňa – vrátené",
    KIND_SURPLUS: "Prebytok výroby",
}

# typ v load.php -- 0 pre požičovňu, inak 1
TYP_BANK = 0
TYP_OTHER = 1

UNIT_KWH = "kWh"
UNIT_EUR = "EUR"

STAT_ID_TEMPLATE = "magna:{kind}"
STAT_ID_BAND_TEMPLATE = "magna:{kind}_{band}"
