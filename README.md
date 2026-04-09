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
4. Render loob automaatselt:
- web teenuse (FastAPI)
- worker teenuse (pending retry + heartbeat)
- PostgreSQL andmebaasi
5. Pärast deployd ava web teenuse Environment ja kontrolli, et BRANCH_BASE_URL oleks sinu Renderi avalik URL (nt https://pangaapi-web.onrender.com).

Kontroll:

- Health: https://<sinu-web-url>/health
- Docs: https://<sinu-web-url>/docs

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

## Live URL

Asenda pärast deployd:

- https://YOUR-RENDER-URL.onrender.com

## GitHub esitamine

Esita repositooriumi link koos selle README ja töötava deploy URL-iga.
