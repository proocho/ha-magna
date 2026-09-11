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

| senzor | jednotka | kedy vznikne |
|---|---|---|
| Spotreba za mesiac | kWh | vždy |
| Náklady v 4T za mesiac | EUR | vždy |
| Prebytok výroby za mesiac | kWh | len s fotovoltikou |
| Vrátené z požičovne za mesiac | kWh | len s požičovňou |
| Zmena požičovne za mesiac | kWh | len s požičovňou |

Senzory pre požičovňu a prebytok sa vytvoria, **len ak tie odberné miesta na
účte naozaj sú**. Väčšina zákazníkov ich nemá a natrvalo prázdne senzory sú
horšie než žiadne.

**Viac odberných miest:** pri pridávaní si vyberieš, ktoré chceš sledovať.
Ďalšie pridáš ako samostatnú integráciu – každé má vlastné zariadenie aj
vlastné štatistiky.

> **Pozor na „Náklady v 4T".** Portál toto číslo počíta každému, aj tomu, kto
> štvortarif nemá – vtedy je to hypotetické „koľko by si platil, keby si
> prešiel". Doslovný popis z portálu je v atribúte `popis`. Spotreba v kWh je
> v oboch pohľadoch rovnaká, líši sa len rozpad a cena.

**Dlhodobé štatistiky** (`external statistics`) – denné rady pre každé odberné
miesto a každé tarifné pásmo, pod `magna:<EIC kód>`. Recorder si k nim vedie kumulatívny
súčet, takže história zostáva aj po reštarte a dá sa použiť v Energy dashboarde.

### Zostatok požičovne

Portál saldo **nezverejňuje** — vie sa z neho vyčítať len mesačný tok dnu
(prebytok výroby) a von (vrátené z požičovne). Absolútnu hladinu preto zadáš
raz z faktúry (položka nespotrebovanej požičanej elektriny) v *Konfigurovať*
a odvtedy sa dopočítava:

```
zostatok = ukotvenie z faktúry + Σ (prebytok − vrátené) za mesiace po ukotvení
```

Nedrží sa pritom žiadny stav — pri každom obnovení sa celý rad prepočíta
z toho, čo povie portál. Do súčtu vstupujú **len mesiace, kde majú dáta oba
toky**: bežiaci mesiac má prebytok a nulové výbery (tie pribudnú až pri
fakturácii), takže by zostatok umelo nafúkol. Koľko mesiacov sa započítalo
a po ktorý, je v atribútoch senzora.

Bez zadaného ukotvenia senzor nevznikne — mesačné toky fungujú aj tak.

> Pôvodne to mal byť template senzor nad štatistikami. Nejde to: **šablóny
> v Home Assistante sa na long-term statistics nevedia pozrieť.** Šlo by to
> len cez SQL senzor nad tabuľkou `statistics`, čo je krehké.

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
4. **`eic` nie je identita, len poradie v rozbaľovačke.** Keď zákazníkovi
   pribudne odberné miesto, indexy sa posunú. Identita sa preto stavia na EIC
   kóde zo začiatku labelu.
5. **`typ` je tarifný pohľad, nie druh miesta.** `radio_standard` má hodnotu
   `1`, `radio_4t` hodnotu `0`.

Súčty sa **neskladajú zo sérií** – portál ich posiela hotové v `text_sumar`,
takže sa zobrazuje presne to, čo vidí užívateľ v portáli.

## Upozornenie

Neoficiálna integrácia, nijako nesúvisí s MAGNA ENERGIA, a.s. Portál sa môže
kedykoľvek zmeniť a integrácia potom prestane fungovať. Sťahuje sa dvakrát
denne – mesačné dáta častejšie nemá zmysel.

## Ikona integrácie

Ikona je priamo v integrácii (`custom_components/magna/brand/`) a Home Assistant
ju od verzie 2026.3.0 servíruje sám cez `/api/brands/integration/magna/icon.png`
([brands proxy API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)).
Do repozitára `home-assistant/brands` sa ikony custom integrácií už neposielajú.

Ikona je **vlastná** – batéria so slnkom, teda uložené slnko. Logo MAGNA ENERGIA
sa tu zámerne nepoužíva, aby integrácia nevyzerala ako oficiálna.

## Licencia

MIT
