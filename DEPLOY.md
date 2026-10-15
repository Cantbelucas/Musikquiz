# Deployment

Antagelser: Docker + Docker Compose v2 på serveren, nginx som reverse proxy (i en container eller direkte på værten), og et Cloudflare wildcard-certifikat som subdomænet arver. Eksemplerne bruger `quiz.example.dk` — erstat med dit subdomæne.

Appen er **helt uafhængig** af dine andre apps:

| Ressource | Værdi |
|---|---|
| Compose-projekt | `musikquiz` |
| Container | `musikquiz` |
| Image | `musikquiz:latest` |
| Docker-netværk | `musikquiz_net` (eget) |
| Port | `127.0.0.1:8091` → container `8000` |
| Data | `./data` → `/data` (SQLite: `data/musikquiz.sqlite3`, lyd: `data/media/`) |
| Bruger i containeren | uid/gid `10001` |
| Udgående trafik | HTTPS til `itunes.apple.com`, `audio-ssl.itunes.apple.com`, `api.deezer.com`, `*.dzcdn.net` (kun for genrer med kilden iTunes) |

---

## 1. Læg koden på serveren

```bash
sudo mkdir -p /opt/musikquiz
sudo chown "$USER" /opt/musikquiz
# kopiér projektet hertil (git clone, rsync, scp …)
cd /opt/musikquiz
```

Tjek at porten er ledig (8090 er optaget af en anden app):

```bash
ss -ltn | grep -E ':8091\b' || echo "8091 er ledig"
```

Er 8091 optaget, så vælg en anden og sæt `HOST_PORT` i `.env`.

## 2. Opret `.env`

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # indsæt som SECRET_KEY
nano .env                                                   # sæt SECRET_KEY og ADMIN_PASSWORD
chmod 600 .env
```

Mindst `SECRET_KEY` og `ADMIN_PASSWORD` skal udfyldes. Behold `SESSION_COOKIE_SECURE=true` og `PROXY_COUNT=1` bag nginx + HTTPS.

`ITUNES_COUNTRY` (standard `DK`) bestemmer hvilket iTunes-katalog der søges i. Der skal ingen API-nøgler til iTunes eller Deezer.

> Har du en firewall med begrænset udgående trafik, skal containeren kunne nå `itunes.apple.com`, `audio-ssl.itunes.apple.com`, `api.deezer.com` og `*.dzcdn.net` på port 443. Previews hentes af serveren og streames videre til browseren — spillernes browsere kontakter aldrig Apple eller Deezer.

## 3. Byg og start

```bash
docker compose up -d --build
docker compose ps                        # STATUS skal blive "healthy"
curl -s http://127.0.0.1:8091/healthz    # {"ok":true}
docker compose logs -f                   # Ctrl+C for at stoppe visning
```

### Om rettigheder på `data/`

Du behøver ikke oprette `data/` selv. Containeren starter som root **kun** i entrypoint-scriptet, som:

1. opretter `/data/media`,
2. retter ejerskab til `10001:10001` hvis noget er forkert (fx når Docker har oprettet `./data` som root),
3. skifter til uid 10001 og starter gunicorn. Gunicorn kører aldrig som root.

Vil du hellere styre det selv (eller køre med `user: "10001:10001"` i compose):

```bash
mkdir -p data/media
sudo chown -R 10001:10001 data
```

## 4. nginx

### A) nginx kører i Docker (antagelsen)

nginx-containeren skal kunne nå `musikquiz` over musikquiz' eget netværk. Forbind **kun nginx** til det — ingen andre apps:

```bash
docker network connect musikquiz_net <navn-på-din-nginx-container>
```

Eller permanent i nginx' egen `docker-compose.yml`:

```yaml
services:
  nginx:
    # … eksisterende opsætning …
    networks:
      - default            # dine eksisterende netværk
      - musikquiz_net

networks:
  musikquiz_net:
    external: true
```

Først en cache til lydklippene. Den skal stå i `http`-blokken (fx i `nginx.conf`), ikke inde i `server`:

```nginx
proxy_cache_path /var/cache/nginx/musikquiz levels=1:2 keys_zone=musikquiz_klip:10m
                 max_size=2g inactive=30d use_temp_path=off;
```

Mount også mediemappen read-only ind i nginx-containeren, så nginx kan sende de uploadede filer:

```yaml
services:
  nginx:
    volumes:
      # … eksisterende mounts …
      - /opt/musikquiz/data/media:/srv/musikquiz-media:ro
```

Server-blok, fx `conf.d/musikquiz.conf`:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name quiz.example.dk;

    # Samme wildcard-certifikat som dine andre subdomæner
    ssl_certificate     /etc/nginx/certs/wildcard.crt;
    ssl_certificate_key /etc/nginx/certs/wildcard.key;

    # Uploads af lydfiler (skal være >= MAX_UPLOAD_MB)
    client_max_body_size 60m;

    location / {
        # Docker's interne DNS: nginx starter også selvom musikquiz er nede
        resolver 127.0.0.11 valid=30s ipv6=off;
        set $musikquiz_upstream http://musikquiz:8000;
        proxy_pass $musikquiz_upstream;

        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;

        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    # Lydklip fra iTunes/Deezer. Hvert klip har en fast URL, så nginx kan gemme det
    # og servere det igen uden at røre hverken appen eller Apple/Deezer.
    location ^~ /klip/ {
        resolver 127.0.0.11 valid=30s ipv6=off;
        set $musikquiz_klip http://musikquiz:8000;
        proxy_pass $musikquiz_klip;

        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Hent altid hele klippet fra appen; nginx svarer selv på Range ud fra cachen
        proxy_set_header Range "";
        proxy_cache musikquiz_klip;
        proxy_cache_valid 200 7d;          # kun 200 caches — døde previews (404) gør ikke
        proxy_cache_lock on;               # samtidige spillere udløser ét hent, ikke ti
        proxy_cache_use_stale updating error timeout;
        add_header X-Cache-Status $upstream_cache_status always;
    }

    # Uploadede filer: appen tjekker adgangen og peger hertil med X-Accel-Redirect,
    # og nginx sender selve filen. Kan ikke kaldes direkte udefra ("internal").
    location /_media/ {
        internal;
        alias /srv/musikquiz-media/;       # mountet fra ./data/media
    }
}

server {
    listen 80;
    server_name quiz.example.dk;
    return 301 https://$host$request_uri;
}
```

```bash
docker exec <nginx-container> nginx -t && docker exec <nginx-container> nginx -s reload
```

### Slå lydleveringen til

Lydklip fra iTunes caches af nginx, så snart `location ^~ /klip/` er på plads — appen skal ikke sættes op til det.

De uploadede filer sendes først af nginx, når du sætter dette i `.env` og genstarter appen:

```bash
X_ACCEL_MEDIA=/_media/
```

Værdien skal matche `location /_media/` præcis. **Tjek bagefter**, at en sang stadig kan spilles — er mountet eller stien forkert, får du 404 på lyden, og så sætter du bare `X_ACCEL_MEDIA=` (tom) igen, hvorefter appen selv streamer filerne som før.

Du kan se at cachen virker ved at kalde det samme klip to gange:

```bash
curl -sI https://quiz.example.dk/klip/1/<signatur> | grep -i x-cache-status   # MISS
curl -sI https://quiz.example.dk/klip/1/<signatur> | grep -i x-cache-status   # HIT
```

(Find en gyldig klip-URL i browserens netværksfane, mens du spiller en runde.)

**Hvorfor det er værd at gøre:** uden det bruger appen én gunicorn-tråd pr. afspilning. Med standardopsætningen (2 workers × 4 tråde) er der kun 8 samtidige afspilninger, før folk venter på lyd. Med nginx foran bruger appen kun en tråd, første gang et klip hentes.

### B) nginx kører direkte på værten

Samme server-blok, men uden `resolver`/`set`, og med `proxy_pass http://127.0.0.1:8091;` i både `location /` og `location ^~ /klip/`. Mediemappen peges direkte på stien:

```nginx
    location /_media/ {
        internal;
        alias /opt/musikquiz/data/media/;
    }
```

nginx-brugeren (typisk `www-data`) skal kunne læse mapperne ovenfor, fx `/opt/musikquiz` og `/opt/musikquiz/data`.

### Rigtig klient-IP bag Cloudflare

Appen bruger klientens IP til at begrænse forkerte admin-logins. Bag Cloudflare er `$remote_addr` en Cloudflare-IP, medmindre nginx har `real_ip` sat op. Hvis du allerede har det for dine andre apps (`set_real_ip_from <Cloudflare-ranges>; real_ip_header CF-Connecting-IP;`), virker ovenstående som det er. Ellers fungerer det stadig — throttlingen gælder så bare pr. Cloudflare-edge i stedet for pr. bruger.

## 5. Cloudflare

1. **DNS** → tilføj `quiz` (CNAME eller A som dine andre subdomæner), **Proxied** (orange cloud).
2. **SSL/TLS** → *Full (strict)* (som dine øvrige subdomæner).
3. Cloudflare's gratis plan tillader uploads op til 100 MB — fint med `MAX_UPLOAD_MB=50`.
   Cloudflare afbryder svar der tager over 100 sekunder (fejl 524). Det kan ske, hvis en spiller starter den allerførste runde i en iTunes-genre med mange søgetermer, som aldrig er hentet. **Forudfyld derfor cachen** for nye iTunes-genrer (se nedenfor).
4. Slå **Rocket Loader** fra for subdomænet, hvis det er slået til — appen har en streng Content-Security-Policy, og Rocket Loader omskriver scripts.

Åbn `https://quiz.example.dk` — og `https://quiz.example.dk/admin/` for at logge ind.

---

## Genrer

Spillerne kan kun vælge genrer, som du har oprettet. Gå til `https://quiz.example.dk/admin/genrer/`.

For hver genre vælger du **én kilde**. Spillerne ser aldrig kilden — kun navnet.

### Genre med kilden "Egne filer"

1. Opret genren med et navn, fx `Dansk undergrund`, og kilden **Egne filer**.
2. Upload sange til den (se *Sådan lægger du sange ind* herunder).

### Genre med kilden "iTunes"

1. Opret genren, fx `Rap`, med kilden **iTunes**.
2. Skriv **søgetermer**, én pr. linje (højst 25). Hver term giver op til 200 sange:

   ```
   artist:Kendrick Lamar
   artist:Drake
   artist:Tyler, The Creator
   hip hop
   ```

   `artist:Navn` søger kun på kunstnernavn og giver de mest præcise genrer. Fri tekst (`hip hop`) søger bredt i titler, album og kunstnere.
3. Valgfrit **iTunes-genrefilter**, fx `Hip-Hop/Rap` — frasorterer iTunes-resultater fra andre genrer. Godt sammen med brede søgetermer.
4. Tryk **Opret genre**, og derefter **Forudfyld cache** på genrens side. Du ser fremskridt pr. søgeterm. iTunes tillader ca. 20 opslag i minuttet, så det tager ca. 3 sekunder pr. term.
5. Gennemgå **Cachede spor**:
   - Sværhedsgraden er sat automatisk ud fra iTunes' resultatrækkefølge (først = Let, sidst = Svær). Vælg en anden i listen for at overskrive — det gemmes med det samme.
   - Tryk **Skjul** på sange, der ikke hører til genren.
   - Fanen **Uden preview** viser sange, hvis preview er forsvundet. De kommer aldrig med i en runde.

Finder iTunes ingen sange med preview for en søgeterm, prøves Deezer automatisk. Kolonnen *Fra* viser, hvor en sang kommer fra.

### Dansk musik

iTunes' danske katalog dækker dansk musik godt: hver dansk kunstner giver typisk ~200 numre med preview. To ting er værd at vide:

- **Brug `artist:`-termer.** Brede danske termer som `dansk rock` rammer alt muligt (fx "Denmark Rocks"), fordi iTunes søger i titler og albumnavne på tværs af sprog.
- **Genrefilteret kan ikke bruges til at finde "dansk".** iTunes bruger engelske genrenavne (`Pop`, `Rock`, `Hip-Hop/Rap`) også for danske kunstnere — der findes ingen "Dansk"-genre at filtrere på. Det er kunstnerlisten, der afgrænser genren.

Eksempler der virker (testet mod iTunes DK):

| Genre | Søgetermer |
|---|---|
| Dansk rock | `artist:Kim Larsen`, `artist:Tv-2`, `artist:Gasolin'`, `artist:D-A-D`, `artist:Nephew`, `artist:Minds of 99`, `artist:Magtens Korridorer` |
| Dansk pop | `artist:Medina`, `artist:Rasmus Seebach`, `artist:Burhan G`, `artist:Christopher`, `artist:Thomas Helmig`, `artist:Andreas Odbjerg`, `artist:Sanne Salomonsen` |
| Dansk rap | `artist:Gilli`, `artist:Suspekt`, `artist:Tessa`, `artist:Ukendt Kunstner`, `artist:L.O.C.`, `artist:Kesi`, `artist:Niarn` |
| Danske klassikere | `artist:Shu-bi-dua`, `artist:Anne Linnet`, `artist:Dodo and the Dodos`, `artist:Kandis`, `artist:Big Fat Snake`, `artist:Lis Sørensen` |

De fire genrer ovenfor giver tilsammen over 3.000 numre, jævnt fordelt på de tre sværhedsgrader. Syv søgetermer tager ca. 25 sekunder at forudfylde.

Vil du have færre, men mere kendte sange i en genre, så brug færre kunstnere: sværhedsgraden følger kunstnerens egen resultatrækkefølge, så med mange kunstnere bliver "Svær" fyldt med b-sider. Du kan også skjule enkelte sange i listen bagefter.

Ændrer du søgetermer eller filter, opdateres listen med det samme fra cachen. Nye termer hentes, når du trykker **Forudfyld cache** (eller ved næste runde i genren). Dine manuelle valg bevares.

### Hold cachen frisk (valgfrit)

Søgninger ældre end `CATALOG_CACHE_DAYS` (standard 30 dage) hentes igen ved næste forudfyldning. Det kan køre automatisk om natten via cron på værten:

```cron
30 4 * * * cd /opt/musikquiz && docker compose exec -T -u 10001:10001 musikquiz flask cache-genres >> /var/log/musikquiz-cache.log 2>&1
```

`flask cache-genres Rap Pop` forudfylder kun de nævnte genrer; `--refresh` henter alt igen uanset alder.

### Regler

- En genre med uploadede sange kan ikke slettes eller skifte kilde, før sangene er flyttet eller slettet.
- Omdøber du en "Egne filer"-genre, følger sangene med.
- Sletter du en iTunes-genre, slettes dens cachede spor. Selve søgeresultaterne bliver i cachen og genbruges, hvis en anden genre bruger samme søgeterm.

---

## Sådan lægger du sange ind

Gælder genrer med kilden **Egne filer**. Opret genren først (se ovenfor).

### Via admin-panelet (en ad gangen)

1. Gå til `https://quiz.example.dk/admin/` og log ind med `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
2. Under **Upload sang**: vælg en `.mp3` eller `.wav`-fil (maks `MAX_UPLOAD_MB`).
   Hedder filen `Artist - Titel.mp3`, udfyldes artist og titel automatisk.
3. Udfyld **titel** og **artist**, vælg **genre** (kun genrer med kilden Egne filer) og **sværhedsgrad** (Let / Mellem / Svær).
4. **Start ved** (valgfrit): hvor i sangen klippene starter, i sekunder (`42`) eller `mm:ss` (`1:05`). Brug det til at springe stille intro over eller starte ved omkvædet.
5. Tryk **Upload**. Genre og sværhedsgrad huskes til næste upload, så det går hurtigt at lægge flere ind i træk.

I biblioteket kan du afspille (▶), redigere og slette sange. Sletning fjerner også lydfilen.

Stavning af artist skal være ens for at sange grupperes — feltet foreslår eksisterende kunstnere.

### Masse-import fra en mappe

1. Kopiér filerne til `data/import/` på serveren, navngivet `Artist - Titel.mp3`:

   ```bash
   mkdir -p /opt/musikquiz/data/import
   cp ~/sange/*.mp3 /opt/musikquiz/data/import/
   sudo chown -R 10001:10001 /opt/musikquiz/data/import
   ```

2. Kør importen **som uid 10001** (vigtigt — ellers ejes filer og database af root, og appen får permission-fejl):

   ```bash
   # Prøv først uden at ændre noget
   docker compose exec -u 10001:10001 musikquiz flask import-dir /data/import --genre Pop --difficulty easy --dry-run

   # Importér
   docker compose exec -u 10001:10001 musikquiz flask import-dir /data/import --genre Pop --difficulty easy
   ```

   Sange med samme titel + artist som en eksisterende springes over. Filerne kopieres ind i `data/media/`, så `data/import/` kan slettes bagefter.

   Findes genren ikke, oprettes den med kilden **Egne filer**. Sange til en genre med kilden iTunes afvises.

3. Forskellige genrer/sværhedsgrader i samme mappe? Brug en CSV (UTF-8) i stedet for `--genre`/`--difficulty`:

   ```csv
   file,title,artist,genre,difficulty,start_at
   Kim Larsen - Midt om natten.mp3,Midt om natten,Kim Larsen,Dansk rock,easy,0:12
   Tv-2 - Kys bruden.mp3,Kys bruden,Tv-2,Dansk pop,medium,
   ```

   ```bash
   docker compose exec -u 10001:10001 musikquiz flask import-dir /data/import --csv /data/import/sange.csv
   ```

### Gode råd om biblioteket

- **Mindst 6 sange pr. kategori/sværhedsgrad** giver de bedste svarmuligheder (ellers hentes forkerte svar fra andre kategorier).
- **Mindst 10 sange** pr. valg giver en fuld runde; færre giver en kortere runde.
- Sværhedsgrad er dit skøn: *Let* = alle kender den, *Mellem* = de fleste har hørt den, *Svær* = for kendere.

---

## Drift

**Opdatering:**

```bash
cd /opt/musikquiz
# hent ny kode …
docker compose up -d --build
```

Databaseskemaet oprettes og migreres automatisk ved start; data i `./data` bevares.

**Opgradering fra versionen uden genrer:** tag en backup først (se herunder). Ved første start oprettes tabellerne til genrer og cache, `tracks` får kolonnen `popularity`, og alle eksisterende genrer på dine uploadede sange bliver til admin-genrer med kilden **Egne filer** (`Rock` og `rock` slås sammen til én). Spillet virker som før; opret derefter iTunes-genrer efter behov. Fjern `TRACK_SOURCE` fra din `.env` — den bruges ikke længere.

**Backup** (kan køre mens appen kører — genrer og iTunes-cache ligger i samme database):

```bash
docker compose exec -u 10001:10001 musikquiz python -c "import sqlite3; sqlite3.connect('/data/musikquiz.sqlite3').backup(sqlite3.connect('/data/backup.sqlite3'))"
tar czf musikquiz-backup-$(date +%F).tar.gz -C /opt/musikquiz/data backup.sqlite3 media
```

**Nulstil highscores:**

```bash
docker compose exec -u 10001:10001 musikquiz python -c "import sqlite3; c=sqlite3.connect('/data/musikquiz.sqlite3'); c.execute('DELETE FROM highscores'); c.commit()"
```

**Stop / fjern:**

```bash
docker compose down          # data i ./data bevares
```

## Fejlfinding

| Symptom | Årsag / løsning |
|---|---|
| Containeren genstarter, log siger `SECRET_KEY mangler` | Udfyld `SECRET_KEY` i `.env` |
| "Ugyldig eller udløbet formular" ved hver login/start | Du tester over `http://` med `SESSION_COOKIE_SECURE=true`, så browseren gemmer ikke cookien. Brug HTTPS-domænet, eller sæt `false` midlertidigt |
| "Ugyldig eller udløbet formular" en enkelt gang | Session udløbet — genindlæs siden |
| `413` ved upload | Øg `client_max_body_size` i nginx og/eller `MAX_UPLOAD_MB` |
| `502 Bad Gateway` | `docker compose ps` — kører containeren? Er nginx forbundet til `musikquiz_net` (`docker network inspect musikquiz_net`)? |
| `unable to open database file` / `Permission denied` | Nogen har kørt en kommando som root i `/data`. Kør `docker compose restart` (entrypoint retter ejerskab) og brug altid `-u 10001:10001` med `exec` |
| Lyden afspilles ikke / klip starter forkert | Tjek at filen spiller under ▶ i admin. Hvis *Start ved* er længere end sangen, er der intet at afspille |
| iTunes-genre har 0 spor / "Kunne ikke hente" | Se fejlen i forudfyldningens log. `Rate-limit ramt` → vent et par minutter og tryk **Hent alt igen** (*Forudfyld* prøver en fejlet søgeterm tidligst igen efter en time). `Kunne ikke kontakte …` → containeren har ikke udgående HTTPS (firewall/DNS) |
| iTunes-genre vises ikke for spillerne | Genren har ingen spillbare sange (alle skjult eller uden preview), eller søgetermerne har aldrig givet resultater. Tjek genrens side i admin |
| `524` fra Cloudflare når en runde startes | Første runde i en genre der aldrig er hentet tog over 100 sekunder. Forudfyld cachen i admin |
| Lyden giver `404` efter du satte `X_ACCEL_MEDIA` | nginx kan ikke finde filerne: `location /_media/` mangler, eller mediemappen er ikke mountet ind i nginx-containeren. Sæt `X_ACCEL_MEDIA=` (tom) for at gå tilbage til at appen streamer selv |
| `X-Cache-Status` siger altid `MISS` | `proxy_cache_path` mangler i `http`-blokken, eller nginx kan ikke skrive i cache-mappen (`/var/cache/nginx/musikquiz`) |
| Lyd fra iTunes-genre giver `502` | Appen kunne ikke nå Apple/Deezers CDN. Tjek udgående trafik fra containeren: `docker compose exec musikquiz python -c "import urllib.request; print(urllib.request.urlopen('https://itunes.apple.com/search?term=test&limit=1').status)"` |
