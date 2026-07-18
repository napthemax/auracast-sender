# Auracast Sender API – för Lovable

En webapp ska styra en lokal Auracast-sändare. API:t kör på användarens Mac och
routar ljud till en USB-dongle (Sennheiser BTD 700) som sänder Auracast till hörapparater.

## Bas-URL
```
https://playlist-returns-vegas-biblical.trycloudflare.com
```
Publik https-tunnel (Cloudflare) till servern som kör lokalt på Mac:en. CORS är öppet (`*`),
så webappen får anropa direkt. Eftersom den är https slipper du mixed-content-problem.

> **OBS:** Tunnel-URL:en ovan ändras varje gång `cloudflared` startas om. Slutar anropen
> fungera – hämta den nya URL:en och uppdatera bas-URL:en i webappen.
> (Lokalt direkt fungerar också: `http://localhost:8765`.)

---

## Endpoints

### `GET /api/health`
Kontrollera att servern är igång.
```json
{ "ok": true, "service": "auracast-sender", "version": "1.0" }
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
  "file_path": null,         // krävs för "file" (från /api/upload)
  "volume": 0.8,             // 0.0–1.0
  "loop": true               // loopa fil
}
```
Svar `200`: `{ "ok": true, "status": {...} }`
Fel `400`: `{ "detail": "Ingen Auracast-dongle hittad..." }`

### `POST /api/stop`
Stoppa sändning. → `{ "ok": true, "status": {...} }`

### `POST /api/volume`
Justera volym live.
```json
{ "volume": 0.5 }   // → { "ok": true, "volume": 0.5 }
```

### `POST /api/upload`
Ladda upp en WAV-fil (multipart, fält `file`). Returnerar `file_path` att skicka till `/api/start`.
```json
{ "ok": true, "file_path": "/.../uploads/musik.wav", "filename": "musik.wav" }
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
1. **Statusindikator** – grön "Sänder" / grå "Inaktiv" (polla `/api/status`).
2. **Dongle-kort** – visa namnet på enheten där `is_auracast=true` från `/api/devices`. Varna om ingen hittas.
3. **Källväljare** – tre knappar/flikar: Mikrofon, Systemljud, Fil.
   - Vid "Mikrofon": dropdown med `inputs`.
   - Vid "Fil": filuppladdning (`/api/upload`) → starta med returnerad `file_path`.
4. **Volymreglage** – 0–100 %, anropar `/api/volume` vid ändring.
5. **VU-mätare** – horisontell bar driven av `level` från `/api/status` (polla var 200 ms).
6. **Start/Stopp-knappar** – `/api/start` och `/api/stop`.

Anropa alltid `/api/health` vid laddning för att bekräfta att servern är igång; visa
annars "Starta Auracast-servern på din Mac först".

---

## Interaktiv dokumentation
När servern kör finns auto-genererad OpenAPI-dokumentation på:
```
http://localhost:8765/docs
```

---

## Mixed-content (https → http://localhost)
Bas-URL:en ovan är redan en **https-tunnel (Cloudflare)**, så mixed-content är löst.
Om du istället pekar webappen mot `http://localhost:8765` och anropen blockeras:
- **Enklast:** använd https-tunnel-URL:en (redan uppsatt).
- **Chrome:** tillåt "Insecure content" för appens domän (hänglåsikon → Webbplatsinställningar).

---

## Om mottagning i hörapparater (kontext, ej kodrelaterat)
API:t styr **sändningen** från dongeln. Om en specifik mottagare (t.ex. hörapparater)
kan ansluta beror på mottagaren och dess assistent-enhet, inte på detta API:
- **ReSound Nexia tar emot Auracast pålitligt via Android**, inte iPhone (iOS-stödet är
  begränsat 2026). Webappen påverkar inte detta – den startar/stoppar bara sändningen.
- Vanliga Auracast-hörlurar/högtalare ansluter via sitt eget "hitta sändning"-läge.

---

## Färdig Lovable-prompt (klistra in i Lovable)

> Bygg en webapp som styr en lokal Auracast-sändare via ett REST-API.
>
> **API-bas-URL:** `https://playlist-returns-vegas-biblical.trycloudflare.com`
> (CORS är öppet. Om URL:en slutar svara visar appen ett fel och låter mig ange en ny bas-URL i en inställningsruta.)
>
> **Skärmen ska ha:**
> 1. En **statusrad** högst upp: grön "Sänder" eller grå "Inaktiv". Hämtas genom att polla `GET /api/status` var 500 ms (`broadcasting`-fältet).
> 2. Ett **dongle-kort**: anropa `GET /api/devices`, visa namnet på utgången där `is_auracast=true`. Om ingen finns, visa varningen "Anslut en Auracast-dongle".
> 3. En **källväljare** (tre flikar): Mikrofon, Systemljud, Fil.
>    - Mikrofon: dropdown från `inputs` i `/api/devices`.
>    - Fil: en filuppladdningsknapp som POSTar WAV till `/api/upload` (multipart, fält `file`) och sparar `file_path` från svaret.
> 4. Ett **volymreglage** 0–100 % som vid ändring POSTar `{ "volume": <0..1> }` till `/api/volume`.
> 5. En **VU-mätare** (horisontell bar) driven av `level` (0–1) från `/api/status`-pollningen.
> 6. **Start-** och **Stopp-knappar**:
>    - Start: `POST /api/start` med JSON-body `{ "source": "mic"|"system"|"file", "volume": <0..1>, "loop": true, "file_path": <om fil> }`. Utelämna `output_device` så väljs dongeln automatiskt.
>    - Stopp: `POST /api/stop`.
> 7. Vid sidladdning: anropa `GET /api/health`. Om det misslyckas, visa "Starta Auracast-servern på din Mac".
>
> Håll designen ren och mörk. Visa felmeddelanden från API:t (fältet `detail` vid HTTP 400) som en notis.

> **Tips:** När Lovable byggt appen, testa den mot den körande servern. Starta en sändning
> från webappen och bekräfta i serverns terminal att en `audio_worker`-process startar.
