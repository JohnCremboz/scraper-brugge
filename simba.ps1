#Requires -Version 5.1
<#
.SYNOPSIS
    Simba — start de Besluitendatabank Scraper
.DESCRIPTION
    Installeert automatisch alle benodigde software bij de eerste keer,
    houdt Python-pakketten steeds up-to-date bij elke volgende run,
    en start de interactieve scraper-wizard.

    Vereisten: Windows 10 of 11 met een werkende internetverbinding.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Navigeer altijd naar de map van dit script, zodat relatieve paden kloppen
Set-Location $PSScriptRoot

# ── Hulpfuncties voor leesbare uitvoer ──────────────────────────────────────

function Write-Step { param([string]$Bericht)
    Write-Host "`n  --> $Bericht" -ForegroundColor Cyan }

function Write-OK { param([string]$Bericht)
    Write-Host "  [OK] $Bericht" -ForegroundColor Green }

function Write-Waarschuwing { param([string]$Bericht)
    Write-Host "  [!]  $Bericht" -ForegroundColor Yellow }

function Write-Fout { param([string]$Bericht)
    Write-Host "`n  [FOUT] $Bericht" -ForegroundColor Red }

function Stop-MetFout { param([string]$Bericht)
    Write-Fout $Bericht
    Write-Host ""
    Read-Host "  Druk op Enter om dit venster te sluiten"
    exit 1
}

# ── Banner ───────────────────────────────────────────────────────────────────

Clear-Host
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor DarkCyan
Write-Host "  ║   Besluitendatabank Scraper  —  Simba  v1    ║" -ForegroundColor DarkCyan
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor DarkCyan
Write-Host ""

# ── Stap 1: Controleer winget ────────────────────────────────────────────────

Write-Step "Controleer winget (Windows pakketbeheer)..."

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Stop-MetFout (
        "winget werd niet gevonden.`n" +
        "  Zorg dat Windows 10 / 11 volledig bijgewerkt is, of installeer" +
        " 'App Installer' via de Microsoft Store en probeer opnieuw."
    )
}

Write-OK "winget is beschikbaar"

# ── Stap 2: Installeer uv (pakketbeheer voor Python) ────────────────────────

Write-Step "Controleer uv (Python-pakketbeheer)..."

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Waarschuwing "uv niet gevonden — wordt nu geïnstalleerd via winget..."

    winget install --id astral-sh.uv --exact --silent `
        --accept-package-agreements --accept-source-agreements

    # Zet exitcode opzij voor PATH-verversing (winget geeft soms 0 én -1978335...)
    $wingetCode = $LASTEXITCODE

    # Ververs de PATH-omgevingsvariabele zodat uv meteen bruikbaar is
    $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('PATH', 'User')

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-MetFout (
            "uv is geïnstalleerd (code $wingetCode) maar nog niet zichtbaar in dit venster.`n" +
            "  Sluit dit venster, open een nieuw PowerShell-venster en start simba.ps1 opnieuw."
        )
    }

    Write-OK "uv succesvol geïnstalleerd"
}

$uvVersie = (uv --version 2>&1).ToString().Trim()
Write-OK "uv is beschikbaar  ($uvVersie)"

# ── Stap 3: Python-pakketten installeren en bijwerken ───────────────────────

Write-Step "Python-pakketten synchroniseren en bijwerken (kan even duren)..."

uv sync --upgrade
if ($LASTEXITCODE -ne 0) {
    Stop-MetFout "uv sync mislukt (exitcode $LASTEXITCODE). Controleer je internetverbinding en probeer opnieuw."
}

Write-OK "Alle Python-pakketten zijn up-to-date"

# ── Stap 4: Playwright Chromium (browser voor SmartCities-gemeenten) ─────────

Write-Step "Controleer Playwright-browser (Chromium)..."

# Haal de geïnstalleerde playwright-versie op
$playwrightVersie = (uv run python -m playwright --version 2>&1).ToString().Trim()

# Vergelijk met de vorige keer (opgeslagen in een marker-bestand)
$markerPad  = Join-Path $PSScriptRoot '.playwright_installed'
$opgeslagen = if (Test-Path $markerPad) {
    (Get-Content $markerPad -Raw).Trim()
} else { '' }

if ($opgeslagen -ne $playwrightVersie) {
    Write-Waarschuwing "Chromium installeren / bijwerken voor $playwrightVersie (eenmalig per versie)..."
    uv run python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Stop-MetFout "Playwright Chromium-installatie mislukt (exitcode $LASTEXITCODE)."
    }
    Set-Content -Path $markerPad -Value $playwrightVersie -Encoding UTF8
    Write-OK "Playwright Chromium geïnstalleerd ($playwrightVersie)"
} else {
    Write-OK "Playwright Chromium is al up-to-date ($playwrightVersie)"
}

# ── Stap 5: Start de interactieve scraper ────────────────────────────────────

Write-Host ""
Write-Host "  Alles gereed. De scraper wordt nu gestart..." -ForegroundColor DarkCyan
Write-Host "  (Gebruik de pijltjestoetsen en Enter om te navigeren.)" -ForegroundColor DarkGray
Write-Host ""

uv run python start.py

# Houd het venster open nadat de scraper is afgesloten
Write-Host ""
Write-Host "  De scraper is afgesloten." -ForegroundColor DarkGray
Read-Host "  Druk op Enter om dit venster te sluiten"
