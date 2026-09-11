# Magna iPortál – elektrina a požičovňa (Home Assistant)

Custom integrácia pre Home Assistant, ktorá číta dáta zo zákazníckeho portálu
**MAGNA ENERGIA** ([iportal.magna-energia.sk](https://iportal.magna-energia.sk/)):
spotrebu po tarifných pásmach, prebytok výroby a hlavne **požičovňu elektriny**
– teda virtuálnu batériu, do ktorej sa banká prebytok z fotovoltiky.

Dáta pochádzajú z **fakturačného IMS meradla**, nie zo striedača. Sú teda
nezávislé od akejkoľvek FVE integrácie a je to presne to, podľa čoho dodávateľ
fakturuje.

## Čo integrácia dáva

**Senzory** (vždy za najnovší mesiac, ktorý má dáta – mesiac je v atribúte
`mesiac`, rozpad po pásmach v ďalších atribútoch):

| senzor | jednotka |
|---|---|
| Spotreba za mesiac | kWh |
| Náklady za mesiac | EUR |
| Prebytok výroby za mesiac | kWh |
| Vrátené z požičovne za mesiac | kWh |
| Zmena požičovne za mesiac | kWh |

**Dlhodobé štatistiky** (`external statistics`) – denné rady pre každé odberné
miesto a každé tarifné pásmo, pod `magna:*`. Recorder si k nim vedie kumulatívny
súčet, takže história zostáva aj po reštarte a dá sa použiť v Energy dashboarde.

### Zostatok požičovne

Integrácia **zámerne nepočíta absolútny zostatok banky**. Portál ho neposkytuje
a počítať ho vlastnou logikou by znamenalo držať stav, ktorý sa môže ticho
rozísť. Namiesto toho platí:

```
zostatok = ukotvenie z faktúry
         + magna:prebytok (kumulatívny súčet)
         − magna:pozicovna (kumulatívny súčet)
```

Ukotvenie je jedno číslo z poslednej faktúry (položka nespotrebovanej
požičanej elektriny). Recorder kumulatívne súčty vedie sám, takže zostatok je
potom jednoduchý template senzor. Menej vlastnej logiky, menej miest, kde sa
to môže rozísť.

## Čo integrácia nedá

- **Živý zostatok neexistuje.** Portál sám hlási, že *priebehy vrátenej
  elektriny pre požičovňu sa generujú pri vystavovaní faktúry, spravidla
  v prvej dekáde nasledujúceho mesiaca.* Údaje o výberoch z banky teda
  zaostávajú aj o päť týždňov. Toto je na strane dodávateľa a nedá sa obísť.
- **Spotreba a výroba chodia D+1.** Portál zobrazuje „aktuálne údaje za
  včerajší deň“.
- **Ceny a produkty sa nečítajú.** Sú v HTML kartách portálu, nie v dátovom
  API.
- **Nič to nepredpovedá.** Je to účtovníctvo minulosti, nie riadiaci vstup.

## Inštalácia

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/proocho/ha-magna`,
kategória *Integration*. Potom Nastavenia → Zariadenia a služby → Pridať
integráciu → **Magna iPortál**.

Prihlasovacie údaje sú rovnaké ako do zákazníckeho portálu Magna. Zadávajú sa
v HA a ukladajú sa do config entry – nikde inde.

## Ako to funguje

Portál nemá dokumentované API. Integrácia používa to isté rozhranie ako jeho
vlastný web:

```
GET  /                    -> PHPSESSID
POST /ajax/login.php      login, heslo
POST /ajax/load.php       chartType, date, interval, option, typ,
                          eic, poradie[], force, granularity
POST /ajax/logout.php
```

Tri veci, ktoré stoja za zmienku, lebo sa na nich dá pošmyknúť:

1. **Bez hlavičiek prehliadača portál vracia HTTP 466.** Treba `User-Agent`,
   `Accept` a `Accept-Language`.
2. **Parameter `option` nie je token**, hoci vyzerá ako dlhý hash – je to
   viditeľný text vybraného odberného miesta. Portál to robí rovnako
   (`mg_get_data()` v `js/scripts.min.js`).
3. **Poradie sérií v odpovedi nezodpovedá poradiu riadkov v súhrne.** Overené
   v demo režime, kde prvá séria patrila druhému riadku tabuľky. Integrácia
   preto páruje podľa `series_names`, a keď ho portál nepošle, podľa toho,
   ktorému súčtu sa séria zhoduje.

Súčty sa **neskladajú zo sérií** – portál ich posiela hotové v `text_sumar`,
takže sa zobrazuje presne to, čo vidí užívateľ v portáli.

## Upozornenie

Neoficiálna integrácia, nijako nesúvisí s MAGNA ENERGIA, a.s. Portál sa môže
kedykoľvek zmeniť a integrácia potom prestane fungovať. Sťahuje sa dvakrát
denne – mesačné dáta častejšie nemá zmysel.

## Licencia

MIT
