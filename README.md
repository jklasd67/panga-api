# Panga harukontori API

See projekt realiseerib harukontori API koos keskpanga integratsiooniga:

- Kasutajate registreerimine ja autentimine Bearer API võtmega
- Kontode loomine ja konto omaniku lookup
- Pangasisesed ja pankadevahelised ülekanded
- Idempotentsus transferId alusel
- Pending retry + timeout/refund loogika
- Keskpangaga registreerimine, heartbeat, bank directory cache, exchange rates
- ES256 JWT allkirjastatud pankadevahelised päringud

## Tehnoloogiad

- Python 3.12
- FastAPI
- PostgreSQL
- Docker Compose
- SQLAlchemy
- HTTPX
- python-jose + cryptography (ES256)

## Mikroteenuste arhitektuur

Süsteem on jagatud vähemalt kaheks iseseisvalt deployeritavaks teenuseks:

1. API teenus
- Avalikud endpointid branch-bank OpenAPI järgi
- Autentimine, kontod, ülekanded
- Pankadevaheline endpoint /api/v1/transfers/receive

2. Worker teenus
- Periodiline heartbeat keskpangale
- Bank directory cache värskendus
- Pending ülekannete retry eksponentsiaalse backoffiga
- 4h timeout + automaatne refund

Infra komponendid:

- PostgreSQL: püsivad andmed
- Redis: reserveeritud sõnumivahetuseks/horisontaalseks laienduseks

## Deploy Renderis

Projektis on olemas Render Blueprint fail [render.yaml](render.yaml).

Sammud:

1. Pushi repositoorium GitHubi.
2. Ava Render Dashboard ja vali New + Blueprint.
3. Vali see repo ja kinnita [render.yaml](render.yaml).
4. Veendu, et repo sisaldab [runtime.txt](runtime.txt) ja [.python-version](.python-version), et Render kasutaks Python 3.12 (mitte 3.14).
5. Render loob automaatselt:
- web teenuse (FastAPI)
- PostgreSQL andmebaasi
6. Tasuta plaani piirangute tõttu töötab heartbeat, directory sync ja pending retry web teenuse sees taustal.
7. Pärast deployd ava web teenuse Environment ja kontrolli, et BRANCH_BASE_URL oleks sinu Renderi avalik URL (nt https://pangaapi-web.onrender.com).

Kontroll:

- Health: https://pangaapi-web.onrender.com/health
- Docs (Swagger UI): https://pangaapi-web.onrender.com/docs

Märkus:

- Kui Renderi tasuta plaan ei luba eraldi workerit, siis see projekt kasutab in-process maintenance loop'i, et hoida heartbeat ja retry loogika töös.

## Andmebaasi skeem

Peamised tabelid:

- users: kasutajad + api_key
- accounts: konto, omanik, valuuta, saldo
- transfers: ülekande olekud, kursid, retry/timeout väljad
- bank_directory_entries: keskpanga kataloogi cache
- branch_config: panga registreering, võtmed, heartbeat info
- replay_nonces: JWT nonce replay kaitse

## Endpointid

Nõutud branch-bank endpointid:

- POST /api/v1/users
- POST /api/v1/users/{userId}/accounts
- GET /api/v1/accounts/{accountNumber}
- POST /api/v1/transfers
- POST /api/v1/transfers/receive
- GET /api/v1/transfers/{transferId}

Lisatud praktilised endpointid:

- GET /api/v1/users/{userId}
- GET /api/v1/users/{userId}/transfers
- GET /health

## Käivitamine

1. Käivita teenused:

```bash
docker compose up --build
```

2. API on saadaval:

- http://localhost:8081
- OpenAPI docs: http://localhost:8081/docs

## Keskpanga integratsioon

Kasutatavad endpointid:

- POST /api/v1/banks
- GET /api/v1/banks
- GET /api/v1/banks/{bankId}
- POST /api/v1/banks/{bankId}/heartbeat
- GET /api/v1/exchange-rates

Kui keskpanga kataloog ajutiselt ei vasta:

- kasutatakse lokaalselt cachetud pangaandmeid
- destination panga kättesaamatuse korral märgitakse transfer pending

## Näidispäringud

Registreerimine:

```bash
curl -X POST http://localhost:8081/api/v1/users \
  -H "Content-Type: application/json" \
  -d '{"fullName":"Jane Doe","email":"jane@example.com"}'
```

Konto loomine:

```bash
curl -X POST http://localhost:8081/api/v1/users/<USER_ID>/accounts \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"currency":"EUR"}'
```

Ülekanne:

```bash
curl -X POST http://localhost:8081/api/v1/transfers \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "transferId":"550e8400-e29b-41d4-a716-446655440000",
    "sourceAccount":"EST12345",
    "destinationAccount":"LAT54321",
    "amount":"100.00"
  }'
```

## Testimine

Käivita testid:

```bash
pytest -q
```

Lisaks soovituslikud käsitsi testid:

- idempotentsus: sama transferId kaks korda
- destination bank down: transfer pending + retry
- timeout: failed_timeout + refund
- interbank receive: ES256 JWT valideerimine

### Testide tulemused

- Automaattestid: 2 passed (`pytest -q`)
- Live health endpoint: OK
- Live Swagger UI: OK
- Swagger UI kaudu testitud vood:
  - kasutaja registreerimine
  - konto loomine
  - konto lookup
  - ülekanne
  - ülekande staatuse päring
  - ülekande ajaloo päring

## Live URL

- API live URL:

- https://pangaapi-web.onrender.com

## Swagger UI

- Swagger UI URL:
- https://pangaapi-web.onrender.com/docs

## GitHub esitamine

Esita repositooriumi link koos selle README, live URL-i ja Swagger UI URL-iga.
