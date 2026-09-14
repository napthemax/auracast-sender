# Auracast Sender API – för Lovable

En webapp ska styra en **lokal** Auracast-sändare. API:t kör på användarens Mac och
routar ljud till en USB-dongle (Sennheiser BTD 700 / FlooGoo FMA120) som sänder
Auracast till hörapparater.

## Bas-URL

```
http://127.0.0.1:8765
```

Servern binder till **localhost** som standard. Kör GUI:t (`python3 main.py`) eller
`python3 server.py` på samma Mac som webappen. `GET /api/health` är öppet för
upptäckt; övriga `/api`-anrop kräver API-nyckel.

> **Ingen publik tunnel.** Tidigare exempel med Cloudflare/`trycloudflare` och
> öppen CORS (`*`) är borttagna med flit. Att exponera ett oautentiserat
> kontroll-API mot internet låter vem som helst starta/stoppa sändning och
> ladda upp filer. Om du ändå sätter en tunnel framför: behåll API-nyckeln,
> sätt `AURACAST_CORS_ORIGINS` till webappens **exakta** origin (inte `*`), och
> räkna med att nyckeln är likvärdig med fjärrstyrning av mikrofonen.

---

## Autentisering

Nyckeln skapas vid första körningen och sparas i:

```
~/.auracast-sender/api_key
```

GUI:t skriver också nyckeln i loggen. Skicka den på **alla** `/api`-anrop utom
`GET /api/health`:

```
Authorization: Bearer <nyckel>
```

eller:

```
X-Api-Key: <nyckel>
```

Lovable-appen ska ha ett inställningsfält för nyckeln (förifyll inte en
hårdkodad nyckel). Läs av `auth: "api_key"` i `/api/health` och visa en tydlig
prompt om nyckeln saknas. `401` betyder saknad/fel nyckel.

CORS tillåter `http://localhost` och `http://127.0.0.1` (valfri port). En hostad
Lovable-origin (`https://….lovable.app`) måste läggas till med
`AURACAST_CORS_ORIGINS=https://din-app.lovable.app`. Hostad https mot
`http://127.0.0.1` blockeras dessutom av mixed-content — använd lokal preview
eller Chrome “Insecure content” för den origin, inte en publik tunnel.

---

## Endpoints

### `GET /api/health` (ingen nyckel)
Kontrollera att servern är igång.
```json
{ "ok": true, "service": "auracast-sender", "version": "1.1", "auth": "api_key" }
```

### `GET /api/devices`
Lista ljudenheter. Auracast-donglar har `is_auracast: true`.
`default_output` är index för den auto-detekterade dongeln (eller `null`).
```json
{
  "inputs": [
    { "index": 3, "name": "MacBook Air-mikrofon", "channels": 1, "samplerate": 48000, "is_auracast": false }
  ],
  "outputs": [
    { "index": 2, "name": "BTD 700", "channels": 2, "samplerate": 48000, "is_auracast": true }
  ],
  "default_output": 2
}
```

### `GET /api/status`
Nuvarande status. **Polla var ~200 ms** för live VU-mätare.
```json
{
  "broadcasting": true,
  "level": 0.42,            // VU-nivå 0.0–1.0
  "elapsed_seconds": 12.5,
  "config": { "source": "mic", "output_device": 2, "volume": 0.8, "loop": true },
  "last_error": null
}
```

### `POST /api/start`
Starta sändning. Utelämna `output_device` för att auto-välja dongeln.
```json
// Body:
{
  "source": "mic",          // "mic" | "system" | "file"
  "output_device": 2,        // valfritt – null = auto-detektera dongle
  "input_device": 3,         // valfritt – för "mic"
  "file_path": null,         // krävs för "file" (endast sökvägar under uploads/, från /api/upload)
  "volume": 0.8,             // 0.0–1.0
  "loop": true               // loopa fil
}
```
Svar `200`: `{ "ok": true, "status": {...} }`
Fel `400`: `{ "detail": "Ingen Auracast-dongle hittad..." }`
Fel `401`: saknad/fel API-nyckel.

`file_path` får **inte** peka på godtyckliga filer på disken — bara filer som
resolveras under appens `uploads/`-katalog.

### `POST /api/stop`
Stoppa sändning. → `{ "ok": true, "status": {...} }`

### `POST /api/volume`
Justera volym live.
```json
{ "volume": 0.5 }   // → { "ok": true, "volume": 0.5 }
```

### `POST /api/upload`
Ladda upp en WAV-fil (multipart, fält `file`, max 50 MB). Returnerar `file_path`
att skicka till `/api/start`.
```json
{ "ok": true, "file_path": "/.../uploads/musik.wav", "filename": "musik.wav", "size_bytes": 12345 }
```

### `GET /api/uploads`
Lista uppladdade filer.

---

## Källtyper (`source`)
| Värde | Beskrivning |
|-------|-------------|
| `mic` | Mikrofon → dongle (live) |
| `system` | Systemljud via BlackHole-loopback → dongle |
| `file` | WAV-fil → dongle |

---

## Förslag på UI (för Lovable-prompten)

Bygg ett gränssnitt med:
1. **Inställningar** – bas-URL (default `http://127.0.0.1:8765`) och API-nyckel.
   Skicka nyckeln som `Authorization: Bearer …` eller `X-Api-Key` på alla anrop utom health.
2. **Statusindikator** – grön "Sänder" / grå "Inaktiv" (polla `/api/status`).
3. **Dongle-kort** – visa namnet på enheten där `is_auracast=true` från `/api/devices`. Varna om ingen hittas.
4. **Källväljare** – tre knappar/flikar: Mikrofon, Systemljud, Fil.
   - Vid "Mikrofon": dropdown med `inputs`.
   - Vid "Fil": filuppladdning (`/api/upload`) → starta med returnerad `file_path`.
5. **Volymreglage** – 0–100 %, anropar `/api/volume` vid ändring.
6. **VU-mätare** – horisontell bar driven av `level` från `/api/status` (polla var 200 ms).
7. **Start/Stopp-knappar** – `/api/start` och `/api/stop`.

Anropa alltid `/api/health` vid laddning för att bekräfta att servern är igång; visa
annars "Starta Auracast-servern på din Mac först". Om health svarar men andra anrop
ger `401`, be användaren klistra in nyckeln från GUI-loggen eller
`~/.auracast-sender/api_key`.

---

## Interaktiv dokumentation
När servern kör finns auto-genererad OpenAPI-dokumentation på:
```
http://127.0.0.1:8765/docs
```
Authorize-knappen i Swagger täcker inte `X-Api-Key` automatiskt — skicka headern
manuellt eller använd curl enligt README.

---

## Mixed-content (https-webapp → http://127.0.0.1)
En hostad https-Lovable-app får inte anropa `http://127.0.0.1` (webbläsaren blockerar).
- **Enklast:** kör webappen som lokal preview mot `http://127.0.0.1:8765`.
- **Chrome:** tillåt "Insecure content" för appens origin (hänglåsikon → Webbplatsinställningar).
- **Inte:** publik Cloudflare-tunnel utan autentisering.

---

## Om mottagning i hörapparater (kontext, ej kodrelaterat)
API:t styr **sändningen** från dongeln. Om en specifik mottagare (t.ex. hörapparater)
kan ansluta beror på mottagaren och dess assistent-enhet, inte på detta API:
- **ReSound Nexia tar emot Auracast pålitligt via Android**, inte iPhone (iOS-stödet är
  begränsat 2026). Webappen påverkar inte detta – den startar/stoppar bara sändningen.
- Vanliga Auracast-hörlurar/högtalare ansluter via sitt eget "hitta sändning"-läge.

---

## Färdig Lovable-prompt (klistra in i Lovable)

> Bygg en webapp som styr en lokal Auracast-sändare via ett REST-API på användarens Mac.
>
> **API-bas-URL (default):** `http://127.0.0.1:8765`
> Låt användaren ändra bas-URL i inställningar. Använd inte någon publik tunnel-URL.
>
> **Auth:** Alla anrop utom `GET /api/health` kräver API-nyckel. Användaren klistrar in
> nyckeln (från Auracast Sender-GUI:ts logg eller filen `~/.auracast-sender/api_key`).
> Skicka `Authorization: Bearer <nyckel>` (fallback: header `X-Api-Key`).
> Om `/api/health` fungerar men andra anrop ger HTTP 401, visa "Klistra in API-nyckeln".
>
> **Skärmen ska ha:**
> 1. En **statusrad** högst upp: grön "Sänder" eller grå "Inaktiv". Hämtas genom att polla `GET /api/status` var 500 ms (`broadcasting`-fältet).
> 2. Ett **dongle-kort**: anropa `GET /api/devices`, visa namnet på utgången där `is_auracast=true`. Om ingen finns, visa varningen "Anslut en Auracast-dongle".
> 3. En **källväljare** (tre flikar): Mikrofon, Systemljud, Fil.
>    - Mikrofon: dropdown från `inputs` i `/api/devices`.
>    - Fil: en filuppladdningsknapp som POSTar WAV till `/api/upload` (multipart, fält `file`) och sparar `file_path` från svaret. Skicka inte godtyckliga lokala sökvägar — bara sökvägen från upload-svaret.
> 4. Ett **volymreglage** 0–100 % som vid ändring POSTar `{ "volume": <0..1> }` till `/api/volume`.
> 5. En **VU-mätare** (horisontell bar) driven av `level` (0–1) från `/api/status`-pollningen.
> 6. **Start-** och **Stopp-knappar**:
>    - Start: `POST /api/start` med JSON-body `{ "source": "mic"|"system"|"file", "volume": <0..1>, "loop": true, "file_path": <om fil> }`. Utelämna `output_device` så väljs dongeln automatiskt.
>    - Stopp: `POST /api/stop`.
> 7. Vid sidladdning: anropa `GET /api/health`. Om det misslyckas, visa "Starta Auracast-servern på din Mac".
>
> Håll designen ren och mörk. Visa felmeddelanden från API:t (fältet `detail` vid HTTP 400/401/413) som en notis.

> **Tips:** När Lovable byggt appen, testa den mot den körande servern på samma Mac.
> Starta en sändning från webappen och bekräfta i serverns terminal att en
> `audio_worker`-process startar.
