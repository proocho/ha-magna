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

# typ v load.php je TARIFNY POHLAD, nie druh odberneho miesta:
#   <input id='radio_standard' name='typ_siete' value='1'>
#   <input id='radio_4t'       name='typ_siete' value='0' checked>
# Posielame vzdy 0 (4T). Portal ma 4T ako predvolbu a pri poziciovni
# aj prebytku vyroby ho vnucuje sam (mg_get_data() tam nastavi typ = 0).
# S typ=1 vrati standardnu tarifu, teda len "Spotreba VT/NT" namiesto
# rozpadu na Noc / Dopoludnie / Popoludnie / Rano-Vecer.
TYP_4T = 0
TYP_STANDARD = 1

UNIT_KWH = "kWh"
UNIT_EUR = "EUR"

# Timeout na jednu poziadavku. Bez neho by sa cakalo na aiohttp default
# (5 minut) a zaseknuty portal by tak drzal cely setup integracie.
REQUEST_TIMEOUT = 60

CONF_POINT_CODE = "point_code"
CONF_POINT_LABEL = "point_label"

# Statistic_id sa stavia na EIC kode z labelu, nie na data-value -- to je len
# poradie v rozbalovacke a posunie sa, ked zakaznikovi pribudne odberne miesto.
STAT_ID_TEMPLATE = "magna:{code}"
STAT_ID_BAND_TEMPLATE = "magna:{code}_{band}"
