# Musikquiz

En selvstændig musikquiz-webapp: spilleren hører først **1 sekund** af en sang, så 2, 5 og 10 sekunder, og skal gætte hvilken sang det er. Jo færre sekunder, jo flere point.

- Python + Flask + gunicorn, server-side Jinja2-templates
- Vanilla JS + CSS i `musikquiz/static/` — ingen Node, intet build-step
- SQLite i `/data` (monteret volume), lydfiler i `/data/media`
- To lydkilder: **egne uploadede filer** og **30-sekunders previews fra iTunes** (Deezer som fallback) — admin vælger kilde pr. genre
- Kører i sin egen Docker-container, på sit eget netværk, bag nginx

Se **[DEPLOY.md](DEPLOY.md)** for opsætning på serveren og hvordan man lægger sange ind.

---

## Spillet

1. Vælg kategori: **Alle**, en bestemt **artist** eller en bestemt **genre** (kun genrer admin har oprettet).
2. Vælg sværhedsgrad: **Let / Mellem / Svær**.
3. En runde er 10 sange (eller færre, hvis der ikke er 10 der matcher).
4. For hver sang trykker man på afspilningsknappen og vælger mellem 6 svarmuligheder:

| Klip  | Point ved rigtigt gæt |
|-------|----------------------:|
| 1 sek | 10 |
| 2 sek | 6  |
| 5 sek | 3  |
| 10 sek| 1  |
| Ikke gættet | 0 |

Forkert gæt eller **skip** → næste, længere klip starter automatisk. Forkerte svar streges ud. Efter 10 sekunder uden rigtigt gæt vises svaret.

Til sidst vises samlet score og en oversigt, og man kan skrive sig på highscorelisten (filtrérbar efter sværhedsgrad).

Spillet er det samme uanset hvor sangene kommer fra: samme regler, samme point, samme highscoreliste. Spilleren ser kun genrenavnet — aldrig om genren bygger på uploadede filer eller iTunes.

**Hvorfor valgmuligheder frem for tekstfelt?** Det er det nemmeste at ramme rigtigt — ingen stavefejl, ingen "The Beatles" vs. "Beatles", og det virker godt på mobil. Med 6 muligheder og 4 forsøg kan man ikke bare eliminere sig frem til svaret. Antallet styres med `CHOICES`.

**Tastatur** (desktop): `1`–`6` gætter, `mellemrum` afspiller, `s` skipper, `Enter` går til næste sang.

Pointtabellen ligger i `STAGES` i [`musikquiz/game.py`](musikquiz/game.py) og vises automatisk korrekt i brugerfladen, hvis den ændres.

---

## Genrer og lydkilder

Kun admin opretter genrer (`/admin/genrer/`). Hver genre har **én kilde**:

| Kilde | Hvad | Bruges til |
|---|---|---|
| **Egne filer** | Mp3/wav-filer admin uploader. Sværhedsgrad sættes manuelt pr. sang. | Niche og custom genrer, fx dansk undergrund |
| **iTunes** | 30-sekunders previews fra iTunes Search API ud fra admins **søgetermer**. Finder iTunes intet med preview for en term, bruges Deezers API. | Brede genrer som Rap, Pop, Country |

Ingen API-nøgler, intet login — kun uautentificerede kald med Pythons `urllib`. Ingen nye afhængigheder.

**Søgetermer** (én pr. linje, højst 25): fri tekst (`rap`) søger bredt; `artist:Kendrick Lamar` søger kun på kunstnernavn og giver de mest præcise genrer. Hver term giver op til 200 sange fra iTunes. Et valgfrit **iTunes-genrefilter** (fx `Hip-Hop/Rap`) frasorterer iTunes-resultater fra andre genrer. Filteret gælder ikke Deezer-resultater, da Deezer ikke oplyser genre i søgninger.

**Sværhedsgrad for iTunes-sange** udledes groft af iTunes' egen resultatrækkefølge: første tredjedel af resultatet for en søgeterm = *Let*, midterste = *Mellem*, sidste = *Svær*. Admin kan overskrive den pr. sang og skjule sange der ikke passer ind.

### Cache

iTunes rate-limiter (ca. 20 kald i minuttet), så alt caches i SQLite:

```
genres           admin-genre: navn, kilde, søgetermer, genrefilter
search_cache     rå søgeresultater (titel, artist, album, preview-URL, artwork, iTunes-genre)
                 pr. (udbyder, søgeterm, land) med fetched_at — slås ALTID op før API'et
track_cache      spillbare sange pr. genre, afledt af search_cache: rang, sværhedsgrad,
                 admins valg (difficulty_override, hidden), popularity, preview-status
itunes_playable  view: kun iTunes-genrer, preview findes, ikke skjult — det eneste spillet ser
```

- **Forudfyld** (knap i admin, eller `flask cache-genres`) henter søgetermer der mangler eller er ældre end `CATALOG_CACHE_DAYS` — én term ad gangen, med throttling på tværs af gunicorn-workers.
- Starter en spiller en runde i en genre der aldrig er hentet, hentes den ved start (langsomt første gang — derfor forudfyldning).
- Et fejlet opslag (fx rate-limit) caches i en time, og tidligere gode resultater beholdes.
- Ændres søgetermer eller filter, genopbygges genrens sange fra cachen uden API-kald. Admins valg bevares.

### Previews der mangler

Et nummer uden preview ender aldrig i en quizrunde:

1. Resultater uden preview-URL (eller med en URL uden for Apples/Deezers CDN) gemmes slet ikke.
2. Før en iTunes-sang bruges i en runde, tjekkes det (højst hver 7. dag) at previewet stadig kan hentes. Døde previews markeres og springes over.
3. Deezers preview-URL'er er signerede og udløber — de fornyes automatisk via Deezers API.
4. Lyden proxies gennem appen med Range-understøttelse. Svarer udbyderen 404/410, markeres sangen.

### Lydlevering

Lyd er det eneste i appen, der flytter mange bytes, så den er lagt uden om Python, hvor det kan lade sig gøre:

| | URL | Hvem sender bytes |
|---|---|---|
| Uploadede filer | `/spil/<runde>/lyd/<nr>` (privat pr. runde) | nginx via `X-Accel-Redirect`, hvis `X_ACCEL_MEDIA` er sat — ellers Flask |
| iTunes/Deezer-previews | `/klip/<id>/<signatur>` (fast pr. sang, cachebar) | Appen første gang, derefter nginx' cache |

Preview-URL'en er fast pr. sang, så nginx og browseren kan genbruge klippet på tværs af runder og spillere. Den er signeret med `SECRET_KEY`, så man ikke kan hente vilkårlige spor, og den nævner ikke kilden — serveren finder selv kilden ud fra signaturen, så spilleren ikke kan se hvor en genres sange kommer fra.

Uden nginx foran (fx lokal udvikling) virker begge dele stadig; så streamer appen selv.

### Popularity (til senere)

`tracks` og `track_cache` har en nullable kolonne `popularity` (0–100). Ingen kilde udfylder den i dag. Et senere lag (fx Spotify client credentials, server-til-server) kan kalde:

```python
from musikquiz.popularity import set_popularity
set_popularity(db, "itunes:123", 78)   # eller "local:5"
```

For iTunes-sange bruges popularity så i stedet for resultatrækkefølgen (≥65 = Let, ≥40 = Mellem, ellers Svær — se `AUTO_DIFFICULTY_SQL` i `db.py`). Admins manuelle valg vinder altid. For uploadede sange gemmes værdien kun; sværhedsgraden forbliver admins valg.

---

## Arkitektur

```
musikquiz/
├── __init__.py          app factory, sikkerheds-headers, fejlsider
├── config.py            al konfiguration fra miljøvariabler
├── db.py                SQLite-skema, migrationer (PRAGMA user_version), transaktioner
├── game.py              quiz-regler: trin, point, runder (kender kun TrackSource)
├── genres.py            admin-genrer: opslag og validering
├── library.py           validering og lagring af uploadede lydfiler
├── popularity.py        hook til et senere popularitets-lag
├── security.py          CSRF, admin-login, throttling af login-forsøg
├── cli.py               `flask import-dir` og `flask cache-genres`
├── sources/
│   ├── base.py          TrackSource-abstraktionen (+ Track, Criteria, Availability)
│   ├── local.py         LocalFileSource: uploadede filer + SQLite
│   ├── itunes.py        ITunesSource: previews + cache (Deezer som fallback)
│   ├── catalog_api.py   HTTP-kald til iTunes/Deezer (kun urllib)
│   ├── router.py        GenreRouter: sender hver genre til sin kilde
│   └── __init__.py      registry over kilder
├── views/
│   ├── public.py        forside, spil, JSON-API, highscores
│   ├── admin.py         login, upload, redigér, slet sange
│   └── admin_genres.py  genrer, kilde, søgetermer, cache
├── templates/           Jinja2
└── static/              css/style.css, js/start.js, js/game.js, js/admin.js
```

### Lydkilde-abstraktionen

Quiz-logikken henter sange gennem én grænseflade, `TrackSource` i [`sources/base.py`](musikquiz/sources/base.py):

```python
source.random_track(Criteria("genre", "Rock", "easy"), exclude={"local:3"})  # -> Track | None
source.count(criteria)
source.distractors(track, criteria, n)      # forkerte svarmuligheder
source.availability()                       # hvad kan spilles (til startskærmen)
source.audio_response(track)                # Flask-response med lyden
```

Spor identificeres med en ref præfikset med kildens navn (`local:12`, `itunes:345`), så refs fra forskellige kilder aldrig kolliderer.

Spillet taler med `GenreRouter` ([`sources/router.py`](musikquiz/sources/router.py)), som selv er en `TrackSource`:

- kategori **genre** → den kilde admin har valgt for genren
- kategori **alle** / **artist** → alle kilder, vægtet efter antal sange (iTunes-artister vises først med mindst 5 sange)
- mangler en kilde forkerte svarmuligheder, fyldes der op fra de andre kilder

Derfor skulle hverken `game.py` eller `local.py` ændres, da iTunes kom til.

**Tilføj endnu en kilde senere:**

1. Opret `musikquiz/sources/<navn>.py` med en klasse der arver fra `TrackSource` (`name = "<navn>"`).
2. Registrér den i `_FACTORIES` og giv den et dansk navn i `SOURCE_LABELS` i `musikquiz/sources/__init__.py`.
3. Vælg kilden på en genre i admin.

Spotify stoppede i slutningen af 2024 med at returnere `preview_url` til nye apps — derfor bruges iTunes til previews.

### Sikkerhed

- Admin-login med brugernavn/adgangskode fra `.env`, konstant-tids-sammenligning, max 5 forkerte forsøg pr. IP pr. 15 min.
- CSRF-token på alle formularer og API-kald.
- Uploads valideres på både filendelse og filens indhold (mp3-header / RIFF-WAVE), gemmes under tilfældigt filnavn.
- Det rigtige svar sendes aldrig til browseren før det er afsløret; point beregnes på serveren.
- Lyd kan kun hentes for den sang, der spilles lige nu i en given runde. iTunes/Deezer-previews proxies gennem appen, så browseren kun taler med appen (CSP forbliver `media-src 'self'`), og preview-URL'er valideres mod udbydernes CDN-domæner.
- Streng Content-Security-Policy (ingen inline scripts), `X-Frame-Options: DENY`, `nosniff`.
- Containeren kører som uid 10001, read-only rootfs, `no-new-privileges`, alle capabilities droppet for gunicorn-processen.

Begrænsning: klippets længde håndhæves i browseren. En teknisk kyndig spiller kan hente hele lydfilen for den aktuelle sang via udviklerværktøjer. Det er en bevidst afvejning for en hyggequiz (server-side klipning ville kræve ffmpeg).

---

## Konfiguration

Alt sættes i `.env` (se [`.env.example`](.env.example)):

| Variabel | Standard | Beskrivelse |
|---|---|---|
| `SECRET_KEY` | — (påkrævet) | Signerer session-cookies |
| `ADMIN_USERNAME` | `admin` | Admin-brugernavn |
| `ADMIN_PASSWORD` | — (påkrævet for admin) | Admin-adgangskode. Tom = admin-login slået fra |
| `HOST_PORT` | `8091` | Port på værten (kun 127.0.0.1) |
| `SESSION_COOKIE_SECURE` | `true` | Sæt `false` kun ved test over ren http |
| `PROXY_COUNT` | `1` | Antal proxies der sætter `X-Forwarded-*` |
| `ROUND_LENGTH` | `10` | Sange pr. runde |
| `CHOICES` | `6` | Svarmuligheder pr. sang |
| `MAX_UPLOAD_MB` | `50` | Max filstørrelse ved upload |
| `X_ACCEL_MEDIA` | (tom) | Intern nginx-sti, fx `/_media/`, så nginx sender uploadede filer. Tom = appen streamer selv |
| `PREVIEW_CACHE_SECONDS` | `604800` | Hvor længe nginx/browseren må gemme et preview-klip |
| `ITUNES_COUNTRY` | `DK` | Landekode til iTunes-søgninger (styrer katalog og previews) |
| `CATALOG_CACHE_DAYS` | `30` | Hvor gamle cachede søgninger må være, før *Forudfyld* henter dem igen |
| `CATALOG_TIMEOUT` | `10` | Timeout i sekunder for kald til iTunes/Deezer |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `2` / `4` | Gunicorn |
| `DATA_DIR` | `/data` | Sættes af docker-compose |

---

## Lokal udvikling (uden Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export SECRET_KEY=dev ADMIN_PASSWORD=dev DATA_DIR=./data SESSION_COOKIE_SECURE=false PROXY_COUNT=0
flask --app wsgi run --debug --port 8091
```

Åbn http://127.0.0.1:8091 — admin på http://127.0.0.1:8091/admin/.

Tests (kalder aldrig de rigtige API'er — HTTP-laget erstattes af en falsk klient):

```bash
pytest -q
```
