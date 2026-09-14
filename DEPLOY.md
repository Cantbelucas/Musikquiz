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

### B) nginx kører direkte på værten

Samme server-blok, men uden `resolver`/`set` og med:

```nginx
        proxy_pass http://127.0.0.1:8091;
```

### Rigtig klient-IP bag Cloudflare

Appen bruger klientens IP til at begrænse forkerte admin-logins. Bag Cloudflare er `$remote_addr` en Cloudflare-IP, medmindre nginx har `real_ip` sat op. Hvis du allerede har det for dine andre apps (`set_real_ip_from <Cloudflare-ranges>; real_ip_header CF-Connecting-IP;`), virker ovenstående som det er. Ellers fungerer det stadig — throttlingen gælder så bare pr. Cloudflare-edge i stedet for pr. bruger.

## 5. Cloudflare

1. **DNS** → tilføj `quiz` (CNAME eller A som dine andre subdomæner), **Proxied** (orange cloud).
2. **SSL/TLS** → *Full (strict)* (som dine øvrige subdomæner).
3. Cloudflare's gratis plan tillader uploads op til 100 MB — fint med `MAX_UPLOAD_MB=50`.
4. Slå **Rocket Loader** fra for subdomænet, hvis det er slået til — appen har en streng Content-Security-Policy, og Rocket Loader omskriver scripts.

Åbn `https://quiz.example.dk` — og `https://quiz.example.dk/admin/` for at logge ind.

---

## Sådan lægger du sange ind

### Via admin-panelet (en ad gangen)

1. Gå til `https://quiz.example.dk/admin/` og log ind med `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
2. Under **Upload sang**: vælg en `.mp3` eller `.wav`-fil (maks `MAX_UPLOAD_MB`).
   Hedder filen `Artist - Titel.mp3`, udfyldes artist og titel automatisk.
3. Udfyld **titel**, **artist**, **genre** og vælg **sværhedsgrad** (Let / Mellem / Svær).
4. **Start ved** (valgfrit): hvor i sangen klippene starter, i sekunder (`42`) eller `mm:ss` (`1:05`). Brug det til at springe stille intro over eller starte ved omkvædet.
5. Tryk **Upload**. Genre og sværhedsgrad huskes til næste upload, så det går hurtigt at lægge flere ind i træk.

I biblioteket kan du afspille (▶), redigere og slette sange. Sletning fjerner også lydfilen.

Stavning af artist og genre skal være ens for at sange grupperes — felterne foreslår eksisterende værdier.

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

Databaseskemaet oprettes automatisk; data i `./data` bevares.

**Backup** (kan køre mens appen kører):

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
