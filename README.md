# Musikquiz

En selvstændig musikquiz-webapp: spilleren hører først **1 sekund** af en sang, så 2, 5 og 10 sekunder, og skal gætte hvilken sang det er. Jo færre sekunder, jo flere point.

- Python + Flask + gunicorn, server-side Jinja2-templates
- Vanilla JS + CSS i `musikquiz/static/` — ingen Node, intet build-step
- SQLite i `/data` (monteret volume), lydfiler i `/data/media`
- Kører i sin egen Docker-container, på sit eget netværk, bag nginx

Se **[DEPLOY.md](DEPLOY.md)** for opsætning på serveren og hvordan man lægger sange ind.

---

## Spillet

1. Vælg kategori: **Alle**, en bestemt **artist** eller en bestemt **genre**.
2. Vælg sværhedsgrad: **Let / Mellem / Svær** (sat manuelt af admin pr. sang).
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

**Hvorfor valgmuligheder frem for tekstfelt?** Det er det nemmeste at ramme rigtigt — ingen stavefejl, ingen "The Beatles" vs. "Beatles", og det virker godt på mobil. Med 6 muligheder og 4 forsøg kan man ikke bare eliminere sig frem til svaret. Antallet styres med `CHOICES`.

**Tastatur** (desktop): `1`–`6` gætter, `mellemrum` afspiller, `s` skipper, `Enter` går til næste sang.

Pointtabellen ligger i `STAGES` i [`musikquiz/game.py`](musikquiz/game.py) og vises automatisk korrekt i brugerfladen, hvis den ændres.

---

## Arkitektur

```
musikquiz/
├── __init__.py          app factory, sikkerheds-headers, fejlsider
├── config.py            al konfiguration fra miljøvariabler
├── db.py                SQLite-skema, forbindelse, transaktioner
├── game.py              quiz-regler: trin, point, runder (kender kun TrackSource)
├── library.py           validering og lagring af uploadede lydfiler
├── security.py          CSRF, admin-login, throttling af login-forsøg
├── cli.py               `flask import-dir` til masse-import
├── sources/
│   ├── base.py          TrackSource-abstraktionen (+ Track, Criteria)
│   ├── local.py         LocalFileSource: uploadede filer + SQLite
│   └── __init__.py      registry — vælges med TRACK_SOURCE
├── views/
│   ├── public.py        forside, spil, JSON-API, highscores
│   └── admin.py         login, upload, redigér, slet
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

Spor identificeres med en ref præfikset med kildens navn (`local:12`), så refs fra forskellige kilder aldrig kolliderer.

**Tilføj en ny kilde (fx Spotify) senere:**

1. Opret `musikquiz/sources/spotify.py` med `class SpotifySource(TrackSource)`, `name = "spotify"`.
2. Registrér den i `_FACTORIES` i `musikquiz/sources/__init__.py`.
3. Sæt `TRACK_SOURCE=spotify` i `.env`.

`game.py`, pointberegning, highscores og views skal ikke ændres. Bemærk: Spotify leverer ikke længere 30-sekunders preview-filer til nye apps, så en Spotify-kilde vil sandsynligvis kræve Web Playback SDK i browseren. I så fald skal `game.js` have en afspiller til den type ud over `<audio>`-elementet — selve spillets regler forbliver de samme.

### Sikkerhed

- Admin-login med brugernavn/adgangskode fra `.env`, konstant-tids-sammenligning, max 5 forkerte forsøg pr. IP pr. 15 min.
- CSRF-token på alle formularer og API-kald.
- Uploads valideres på både filendelse og filens indhold (mp3-header / RIFF-WAVE), gemmes under tilfældigt filnavn.
- Det rigtige svar sendes aldrig til browseren før det er afsløret; point beregnes på serveren.
- Lyd kan kun hentes for den sang, der spilles lige nu i en given runde.
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
| `TRACK_SOURCE` | `local` | Lydkilde |
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

Tests:

```bash
pytest -q
```
