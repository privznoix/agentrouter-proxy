# AgentRouter Direct Proxy

Proxy lokal berbasis FastAPI untuk meneruskan request OpenAI-compatible Chat Completions ke AgentRouter.

Endpoint default: `http://127.0.0.1:4020/v1`

## Prerequisite

- Windows 10/11.
- PowerShell 5.1 atau lebih baru.
- Python 3.11 atau lebih baru.
- API key AgentRouter yang aktif.
- Koneksi internet ke AgentRouter.
- Proyek ditempatkan di `C:\Tools\agentrouter-proxy`.

> Skrip PowerShell menggunakan path `C:\Tools\agentrouter-proxy` secara langsung. Jika proyek disimpan di lokasi lain, sesuaikan `$ProjectDir` pada seluruh file `*.ps1`.

## Instalasi

Masuk ke folder proyek:

```powershell
cd C:\Tools\agentrouter-proxy
```

Buat dan aktifkan virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Jika aktivasi diblokir execution policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Pasang dependency:

```powershell
python -m pip install --upgrade pip
python -m pip install fastapi uvicorn httpx
```

## Instalasi via npm (komputer lain)

Untuk komputer lain yang sudah punya Node.js 18+ dan Python 3.11+:

```powershell
npm install -g --allow-git=all github:privznoix/agentrouter-proxy
```

> **Catatan npm 12+:** Flag `--allow-git=all` wajib karena npm 12 menonaktifkan pengambilan paket dari git secara default. Atau jalankan `npm config set allow-git all` sekali di mesin tersebut.

Salin `.env.example` ke `~/.agentrouter-proxy/.env` (atau letakkan `.env` di direktori kerja), lalu isi `AGENTROUTER_API_KEY`. Prioritas pembacaan: environment variable proses → `./.env` → `~/.agentrouter-proxy/.env`.

Perintah CLI (setara script PowerShell, lintas OS):

| Perintah | Fungsi |
| --- | --- |
| `agentrouter-proxy start` | Membuat venv otomatis (sekali) lalu menjalankan proxy di background. Opsi: `-H <host>`, `-p <port>`, `--foreground`. |
| `agentrouter-proxy stop` | Menghentikan proxy. |
| `agentrouter-proxy restart` | Stop lalu start. |
| `agentrouter-proxy status` | Status proses dan port. |
| `agentrouter-proxy doctor` | Diagnostik: Node, Python, venv, `.env`, port, konektivitas upstream. |

Data runtime (venv, log, PID) disimpan di `~/.agentrouter-proxy/` sehingga aman dari update package.

## Konfigurasi

Simpan API key AgentRouter sebagai environment variable user Windows:

```powershell
[Environment]::SetEnvironmentVariable(
    "AGENTROUTER_API_KEY",
    "masukkan-api-key-agentrouter",
    "User"
)
```

Tutup dan buka kembali PowerShell, lalu verifikasi:

```powershell
$env:AGENTROUTER_API_KEY
```

Environment variable utama:

| Variable | Wajib | Default | Keterangan |
| --- | --- | --- | --- |
| `AGENTROUTER_API_KEY` | Ya | - | API key upstream AgentRouter. |
| `AGENTROUTER_PROXY_API_KEY` | Tidak | `local-agentrouter` | Bearer token proxy lokal. |
| `AGENTROUTER_BASE_URL` | Tidak | `https://agentrouter.org/v1` | Base URL upstream. |
| `AGENTROUTER_USER_AGENT` | Tidak | `codex_cli_rs/1.0.0 (Windows; x86_64)` | User-Agent upstream. |
| `AGENTROUTER_PROXY_HOST` | Tidak | `127.0.0.1` | Host saat dijalankan manual. |
| `AGENTROUTER_PROXY_PORT` | Tidak | `4020` | Port saat dijalankan manual. |
| `AGENTROUTER_DEBUG` | Tidak | `true` | Mengaktifkan log diagnostik. |
| `AGENTROUTER_COMPRESS_TOOL_OUTPUT` | Tidak | `true` | Mengompresi output tool besar. |
| `AGENTROUTER_STRIP_IMAGES` | Tidak | `auto` | Pilihan: `auto`, `always`, atau `off`. |
| `AGENTROUTER_DROP_REASONING_EFFORT` | Tidak | `auto` | Saat upstream 400 karena kombinasi function tools + `reasoning_effort` (mis. `gpt-6-astra`), retry sekali dengan `reasoning_effort="none"` + User-Agent alternatif, lalu ingat model tersebut. Pilihan: `auto` atau `off`. |
| `AGENTROUTER_ALT_USER_AGENT` | Tidak | `opencode/1.0.0` | User-Agent alternatif untuk retry `reasoning_effort` (gateway menyuntikkan `reasoning_effort` untuk client bergaya codex). |
| `AGENTROUTER_SANITIZE_MODERATION` | Tidak | `auto` | Sanitizer untuk error keyword upstream `sensitive_words_detected`. `auto`: defang frasa pemicu known sebelum kirim + retry sekali dengan pemisahan kata (zero-width space) jika masih kena; `known`: hanya defang frasa known; `off`: mati. |
| `AGENTROUTER_MODERATION_TRIGGERS_EXTRA` | Tidak | (kosong) | Frasa pemicu tambahan untuk defang pre-send, dipisah `||`. |
| `AGENTROUTER_TRANSLATE_CONTENT_BLOCKED` | Tidak | `auto` | Auto-translate pesan user berbahasa Indonesia ke Bahasa Inggris saat upstream mengembalikan HTTP 400 `content-blocked` (false-positive guardrail gateway), lalu retry sekali dengan instruksi agar asisten tetap merespons dalam Bahasa Indonesia. Pilihan: `auto` atau `off`. |
| `AGENTROUTER_CAPTURE_MODERATION` | Tidak | `true` | Menyimpan payload moderation error. |
| `AGENTROUTER_LOG_DIR` | Tidak | `<folder proyek>\logs` | Lokasi log aplikasi dan `moderation-captures` (dipakai launcher npm). |
| `AGENTROUTER_MODELS_CACHE_TTL` | Tidak | `300` | Umur cache daftar model dari upstream (detik). |

## Daftar Model Dinamis

Proxy tidak lagi mendefinisikan model secara hardcoded. Daftar model diambil dari endpoint upstream `GET /v1/models` dan di-cache selama `AGENTROUTER_MODELS_CACHE_TTL` detik (default 300).

Perilaku:

- ID model yang diekspos ke client diberi prefix `arp/` (contoh: `arp/gpt-5.6-sol`), diarahkan ke model upstream `gpt-5.6-sol`.
- Request dengan ID model upstream tanpa prefix juga diterima.
- Model baru di sisi provider otomatis tersedia setelah cache kedaluwarsa, tanpa perubahan kode.
- Jika upstream `/v1/models` gagal diakses, proxy tetap melayani daftar cache terakhir (stale). Jika belum ada cache sama sekali, proxy mengembalikan HTTP `502`.

Untuk mengganti bearer token lokal:

```powershell
[Environment]::SetEnvironmentVariable(
    "AGENTROUTER_PROXY_API_KEY",
    "api-key-proxy-lokal-baru",
    "User"
)
```

## Menjalankan Proxy

Cara yang direkomendasikan:

```powershell
cd C:\Tools\agentrouter-proxy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-agentrouter-proxy.ps1
```

Proxy berjalan di background pada `http://127.0.0.1:4020/v1`.

Untuk menjalankan langsung di terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agentrouter-proxy:app --host 127.0.0.1 --port 4020 --workers 1
```

Tekan `Ctrl+C` untuk menghentikan mode manual.

## Verifikasi

Cek health endpoint menggunakan bearer token default:

```powershell
$headers = @{ Authorization = "Bearer local-agentrouter" }
Invoke-RestMethod -Uri "http://127.0.0.1:4020/health" -Headers $headers
```

Cek model yang tersedia:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:4020/v1/models" -Headers $headers
```

Contoh chat completion:

```powershell
$body = @{
    model = "arp/gpt-5.6-sol"
    messages = @(
        @{ role = "user"; content = "Halo" }
    )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:4020/v1/chat/completions" -Headers $headers -ContentType "application/json" -Body $body
```

## Stop, Restart, dan Auto Start

Stop proxy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop-agentrouter-proxy.ps1
```

Restart proxy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart-agentrouter-proxy.ps1
```

Pasang Scheduled Task agar proxy berjalan saat user login:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-agentrouter-autostart.ps1
```

Scheduled Task yang dibuat bernama `AgentRouter Direct Proxy`.

## Log

| Lokasi | Isi |
| --- | --- |
| `logs\agentrouter-proxy.log` | Log aplikasi dan request proxy. |
| `logs\uvicorn.log` | Standard output Uvicorn. |
| `logs\uvicorn-error.log` | Error Uvicorn/startup. |
| `logs\moderation-captures` | Payload diagnostik moderation error. |

Jika proxy gagal berjalan, periksa `logs\uvicorn-error.log` dan pastikan `AGENTROUTER_API_KEY` tersedia pada sesi PowerShell.

## Catatan API

- Endpoint aktif: `GET /health`, `GET /v1/models`, dan `POST /v1/chat/completions`.
- Semua endpoint membutuhkan header `Authorization: Bearer <AGENTROUTER_PROXY_API_KEY>`.
- `GET /v1/models` mem-proxy daftar model dari upstream `GET /v1/models` dengan prefix `arp/`.
- `POST /v1/responses` belum diimplementasikan dan mengembalikan HTTP `501`.
