"""
Base scraper module — gedeelde logica voor alle scrapers.

Dit bestand bevat:
- Veilige bestandsnaam-sanitizatie (met path traversal bescherming)
- HTTP sessie met rate limiting en retries (sync + async)
- Parallelle download-functionaliteit
- Gemeenschappelijke hulpfuncties

Gebruik (sync):
    from base_scraper import (
        create_session, sanitize_filename, download_document,
        DownloadResult, ScraperConfig,
    )

Gebruik (async):
    from base_scraper import (
        create_async_session, async_rate_limit, async_download_document,
        async_download_documents_parallel, DownloadResult, ScraperConfig,
    )
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urljoin, urlparse

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Logging configuratie
# ---------------------------------------------------------------------------

logger = logging.getLogger("scraper")

# Standaard: alleen INFO en hoger naar console
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(levelname)s] %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def set_log_level(level: int | str) -> None:
    """Stel het log-niveau in (DEBUG, INFO, WARNING, ERROR)."""
    logger.setLevel(level)


# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

@dataclass
class ScraperConfig:
    """Configuratie voor een scraper-instantie."""
    base_url: str
    output_dir: Path = field(default_factory=lambda: Path("pdfs"))

    # HTTP instellingen
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    timeout: int = 60
    max_retries: int = 3
    backoff_factor: float = 0.5

    # Download instellingen
    max_parallel_downloads: int = 8
    rate_limit_delay: float = 0.2  # Minimale wachttijd tussen requests

    # Bestandsnaam limieten
    max_filename_length: int = 180

    # Inhoudfilter: verwijder PDFs zonder mandaatgerelateerde inhoud.
    # Staat standaard AAN voor GDPR-proportionaliteit.
    # Opt-out via --geen-inhoudfilter in scraper_groep.py of individuele scrapers.
    content_filter: bool = True

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        import os
        if os.environ.get("SCRAPER_GEEN_INHOUDFILTER", "").strip() == "1":
            self.content_filter = False


# ---------------------------------------------------------------------------
# Sync HTTP — requests (behouden voor niet-gemigreerde scrapers)
# ---------------------------------------------------------------------------

_last_request_time: float = 0.0
_rate_limit_lock = threading.Lock()


def create_session(config: ScraperConfig) -> requests.Session:
    """
    Maak een HTTP sessie met retry-logica en rate limiting.

    Retries:
    - 3 pogingen bij 429 (Too Many Requests), 500, 502, 503, 504
    - Exponentiële backoff: 0.5s, 1s, 2s

    Rate limiting:
    - Minimaal `config.rate_limit_delay` seconden tussen requests
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    })

    retry_strategy = Retry(
        total=config.max_retries,
        backoff_factor=config.backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def rate_limited_get(
    session: requests.Session,
    url: str,
    config: ScraperConfig,
    **kwargs,
) -> requests.Response:
    """
    Voer een GET request uit met rate limiting.

    Garandeert minimaal `config.rate_limit_delay` seconden
    tussen opeenvolgende requests.
    """
    global _last_request_time

    with _rate_limit_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < config.rate_limit_delay:
            time.sleep(config.rate_limit_delay - elapsed)
        _last_request_time = time.time()

    kwargs.setdefault("timeout", config.timeout)
    response = session.get(url, **kwargs)

    return response


def robust_get(
    session: requests.Session,
    url: str,
    retries: int = 3,
    timeout: int = 20,
    delay_factor: float = 1.5,
) -> requests.Response | None:
    """
    HTTP GET met retry-logica en exponentiële backoff.

    Returns None bij permanente fouten; logt alleen bij laatste poging.
    """
    for poging in range(retries):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if poging == retries - 1:
                logger.warning("GET mislukt %s: %s", url, exc)
                return None
            time.sleep(delay_factor * (poging + 1))


# ---------------------------------------------------------------------------
# Bestandsnaam sanitizatie (met path traversal bescherming)
# ---------------------------------------------------------------------------

# Karakters die niet toegestaan zijn in bestandsnamen (Windows + Unix)
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Meerdere underscores samenvoegen
_MULTI_UNDERSCORE = re.compile(r'_+')
# Path traversal patronen
_PATH_TRAVERSAL = re.compile(r'\.{2,}')


def sanitize_filename(name: str, max_length: int = 180) -> str:
    """
    Maak een bestandsnaam veilig voor opslag.

    Beschermt tegen:
    - Ongeldige karakters (Windows/Unix)
    - Path traversal aanvallen (.., ..., etc.)
    - Te lange bestandsnamen
    - Unicode normalisatie-aanvallen

    Args:
        name: De originele bestandsnaam
        max_length: Maximale lengte (standaard 180)

    Returns:
        Veilige bestandsnaam, of "document" als input leeg is
    """
    if not name:
        return "document"

    # Normaliseer Unicode (voorkom varianten van dezelfde karakters)
    import unicodedata
    name = unicodedata.normalize("NFKC", name)

    # Verwijder path traversal patronen EERST
    name = _PATH_TRAVERSAL.sub("_", name)

    # Vervang ongeldige karakters
    name = _UNSAFE_CHARS.sub("_", name)

    # Verwijder voorloop/naloop punten en spaties (Windows-probleem)
    name = name.strip(". \t\n\r")

    # Comprimeer meerdere underscores
    name = _MULTI_UNDERSCORE.sub("_", name)

    # Verwijder voorloop/naloop underscores
    name = name.strip("_")

    # Controleer op gereserveerde namen (Windows)
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    name_upper = name.upper().split(".")[0]
    if name_upper in reserved:
        name = f"_{name}"

    # Beperk lengte (behoud extensie indien mogelijk)
    if len(name) > max_length:
        if "." in name:
            base, ext = name.rsplit(".", 1)
            ext = ext[:10]  # Max 10 chars voor extensie
            max_base = max_length - len(ext) - 1
            name = f"{base[:max_base]}.{ext}"
        else:
            name = name[:max_length]

    return name if name else "document"


def safe_output_path(
    base_dir: Path,
    *parts: str,
    filename: str,
    max_filename_length: int = 180,
) -> Path:
    """
    Construeer een veilig uitvoerpad.

    Garandeert dat het resulterende pad binnen base_dir blijft
    (bescherming tegen path traversal).

    Args:
        base_dir: De basis-uitvoermap
        *parts: Optionele subdirectory-onderdelen
        filename: De bestandsnaam
        max_filename_length: Max lengte voor de bestandsnaam

    Returns:
        Absoluut pad binnen base_dir

    Raises:
        ValueError: Als het pad buiten base_dir zou vallen
    """
    # Sanitize alle onderdelen
    safe_parts = [sanitize_filename(p) for p in parts if p]
    safe_filename = sanitize_filename(filename, max_filename_length)

    # Construeer het pad
    target = base_dir
    for part in safe_parts:
        target = target / part
    target = target / safe_filename

    # Resolve naar absoluut pad en controleer
    base_resolved = base_dir.resolve()
    target_resolved = target.resolve()

    # Controleer dat target binnen base blijft
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(
            f"Path traversal gedetecteerd: {target} valt buiten {base_dir}"
        )

    return target


def validate_url(url: str) -> bool:
    """
    Valideer dat een URL een bruikbaar HTTP(S) doel is.
    """
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


# ---------------------------------------------------------------------------
# Document download
# ---------------------------------------------------------------------------

@dataclass
class DownloadResult:
    """Resultaat van een download-poging."""
    url: str
    success: bool
    path: Path | None = None
    error: str | None = None
    skipped: bool = False    # True als bestand al bestond
    filtered: bool = False   # True als weggefilterd door inhoudfilter


def _extract_filename(
    headers: Mapping[str, str],
    hint: str,
    url: str,
) -> str:
    """Haal bestandsnaam uit response headers, hint, of URL."""
    # 1. Probeer Content-Disposition header
    cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
    if "filename=" in cd:
        # RFC 5987 UTF-8 encoding
        match = re.search(r"filename\*=utf-8''([^\s;]+)", cd, re.IGNORECASE)
        if match:
            from urllib.parse import unquote
            return unquote(match.group(1))

        # Standaard filename
        match = re.search(r'filename=["\']?([^"\';\n]+)', cd)
        if match:
            return match.group(1).strip().strip('"\'')

    # 2. Gebruik hint
    if hint:
        return hint

    # 3. Haal uit URL
    path = urlparse(url).path
    return path.split("/")[-1] or "document"


def download_document(
    session: requests.Session,
    config: ScraperConfig,
    doc_url: str,
    output_dir: Path,
    filename_hint: str = "",
    require_pdf: bool = True,
) -> DownloadResult:
    """
    Download een document naar de output-directory.

    Args:
        session: HTTP sessie
        config: Scraper configuratie
        doc_url: URL van het document
        output_dir: Doelmap
        filename_hint: Suggestie voor bestandsnaam
        require_pdf: Alleen PDF's accepteren (standaard True)

    Returns:
        DownloadResult met status en pad
    """
    # Maak absolute URL
    full_url = urljoin(config.base_url, doc_url) if not doc_url.startswith("http") else doc_url
    if not validate_url(full_url):
        return DownloadResult(
            url=full_url,
            success=False,
            error="Ongeldige document-URL",
        )

    # Skip-existing check op basis van filename_hint — vóór HTTP-request
    if filename_hint:
        _hint_clean = sanitize_filename(filename_hint)
        _hint_path = output_dir / _hint_clean
        if _hint_path.exists():
            return DownloadResult(
                url=full_url,
                success=True,
                path=_hint_path,
                skipped=True,
            )

    try:
        resp = rate_limited_get(session, full_url, config, stream=True, allow_redirects=True)

        if resp.status_code != 200:
            return DownloadResult(
                url=full_url,
                success=False,
                error=f"HTTP {resp.status_code}",
            )

        filename = _extract_filename(dict(resp.headers), filename_hint, doc_url)

        # Blokkeer audio/video
        _GEBLOKKEERDE_EXTENSIES = {".mp3", ".wav", ".mp4", ".avi", ".mkv", ".ogg", ".flac"}
        if any(filename.lower().endswith(ext) for ext in _GEBLOKKEERDE_EXTENSIES):
            return DownloadResult(
                url=full_url,
                success=False,
                filtered=True,
                error=f"Geblokkeerd bestandstype: {Path(filename).suffix}",
            )

        # Voeg extensie toe indien nodig
        if "." not in filename[-6:]:
            content_type = resp.headers.get("content-type", "")
            if "pdf" in content_type:
                filename += ".pdf"
            elif "html" in content_type:
                return DownloadResult(
                    url=full_url,
                    success=False,
                    error="HTML response (geen document)",
                )
            else:
                filename += ".bin"

        # Construeer veilig pad
        try:
            output_path = safe_output_path(
                output_dir,
                filename=filename,
                max_filename_length=config.max_filename_length,
            )
        except ValueError as e:
            return DownloadResult(
                url=full_url,
                success=False,
                error=str(e),
            )

        # Maak directory aan
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Zorg voor unieke bestandsnaam als pad al bestaat
        if output_path.exists():
            stem = output_path.stem
            suffix = output_path.suffix
            counter = 2
            while output_path.exists():
                output_path = output_path.parent / f"{stem}_{counter}{suffix}"
                counter += 1

        # Download in chunks, valideer eerste chunk
        chunks = []
        first_chunk = None

        for chunk in resp.iter_content(8192):
            if chunk:
                if first_chunk is None:
                    first_chunk = chunk
                    if require_pdf and not chunk.startswith(b"%PDF"):
                        return DownloadResult(
                            url=full_url,
                            success=False,
                            error="Niet een geldig PDF-bestand",
                        )
                chunks.append(chunk)

        if not chunks:
            return DownloadResult(
                url=full_url,
                success=False,
                error="Lege response",
            )

        # Inhoudfilter — controleer vóór wegschrijven
        if config.content_filter:
            from mandaat_filter import is_relevant_bytes
            content = b"".join(chunks)
            relevant, _ = is_relevant_bytes(content)
            if not relevant:
                return DownloadResult(
                    url=full_url,
                    success=False,
                    filtered=True,
                    error="Gefilterd: geen mandaatrelevante inhoud",
                )
            chunks = [content]

        # Schrijf atomisch (naar tijdelijk bestand, dan rename)
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            with open(temp_path, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)
            temp_path.replace(output_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return DownloadResult(
            url=full_url,
            success=True,
            path=output_path,
        )

    except requests.RequestException as e:
        return DownloadResult(
            url=full_url,
            success=False,
            error=f"Request error: {type(e).__name__}: {e}",
        )
    except Exception as e:
        logger.exception("Onverwachte fout bij download %s", full_url)
        return DownloadResult(
            url=full_url,
            success=False,
            error=f"Onverwachte fout: {type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Parallelle downloads (sync)
# ---------------------------------------------------------------------------

def download_documents_parallel(
    session: requests.Session,
    config: ScraperConfig,
    documents: list[dict],
    output_dir: Path,
    require_pdf: bool = True,
    progress_callback: Callable[[DownloadResult], None] | None = None,
) -> list[DownloadResult]:
    """
    Download meerdere documenten parallel.

    Args:
        session: HTTP sessie
        config: Scraper configuratie
        documents: Lijst van {"url": str, "naam": str} dicts
        output_dir: Doelmap
        require_pdf: Alleen PDF's accepteren
        progress_callback: Optionele callback na elke download

    Returns:
        Lijst van DownloadResult objecten
    """
    if not documents:
        return []

    results: list[DownloadResult] = []

    with ThreadPoolExecutor(max_workers=config.max_parallel_downloads) as executor:
        futures = {
            executor.submit(
                download_document,
                session,
                config,
                doc["url"],
                output_dir,
                doc.get("naam", ""),
                require_pdf,
            ): doc
            for doc in documents
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            if progress_callback:
                progress_callback(result)

    return results


# ---------------------------------------------------------------------------
# Async HTTP — aiohttp
# ---------------------------------------------------------------------------

_async_last_request_time: float = 0.0
_async_rate_limit_lock: asyncio.Lock | None = None


def _get_async_lock() -> asyncio.Lock:
    global _async_rate_limit_lock
    if _async_rate_limit_lock is None:
        _async_rate_limit_lock = asyncio.Lock()
    return _async_rate_limit_lock


def create_async_session(config: ScraperConfig) -> aiohttp.ClientSession:
    """
    Maak een async aiohttp sessie.

    Gebruik als context manager of sluit expliciet met await session.close().
    TCPConnector limit = max_parallel_downloads * 4 om voldoende
    verbindingen te hebben voor gelijktijdige requests.
    """
    connector = aiohttp.TCPConnector(limit=config.max_parallel_downloads * 4)
    return aiohttp.ClientSession(
        connector=connector,
        headers={
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
        },
        timeout=aiohttp.ClientTimeout(total=config.timeout),
    )


async def async_rate_limit(config: ScraperConfig) -> None:
    """Wacht indien nodig om rate limit te respecteren."""
    global _async_last_request_time
    lock = _get_async_lock()
    async with lock:
        elapsed = time.time() - _async_last_request_time
        if elapsed < config.rate_limit_delay:
            await asyncio.sleep(config.rate_limit_delay - elapsed)
        _async_last_request_time = time.time()


async def async_download_document(
    session: aiohttp.ClientSession,
    config: ScraperConfig,
    doc_url: str,
    output_dir: Path,
    filename_hint: str = "",
    require_pdf: bool = True,
) -> DownloadResult:
    """
    Download een document asynchroon naar de output-directory.

    Identiek aan download_document maar gebruikt aiohttp.
    CPU-bound content filter wordt in een executor gedraaid.
    """
    full_url = urljoin(config.base_url, doc_url) if not doc_url.startswith("http") else doc_url
    if not validate_url(full_url):
        return DownloadResult(url=full_url, success=False, error="Ongeldige document-URL")

    if filename_hint:
        _hint_clean = sanitize_filename(filename_hint)
        _hint_path = output_dir / _hint_clean
        if _hint_path.exists():
            return DownloadResult(url=full_url, success=True, path=_hint_path, skipped=True)

    try:
        await async_rate_limit(config)
        async with session.get(full_url, allow_redirects=True) as resp:
            if resp.status != 200:
                return DownloadResult(url=full_url, success=False, error=f"HTTP {resp.status}")

            filename = _extract_filename(dict(resp.headers), filename_hint, doc_url)

            _GEBLOKKEERDE_EXTENSIES = {".mp3", ".wav", ".mp4", ".avi", ".mkv", ".ogg", ".flac"}
            if any(filename.lower().endswith(ext) for ext in _GEBLOKKEERDE_EXTENSIES):
                return DownloadResult(
                    url=full_url, success=False, filtered=True,
                    error=f"Geblokkeerd bestandstype: {Path(filename).suffix}",
                )

            if "." not in filename[-6:]:
                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type:
                    filename += ".pdf"
                elif "html" in content_type:
                    return DownloadResult(url=full_url, success=False, error="HTML response (geen document)")
                else:
                    filename += ".bin"

            try:
                output_path = safe_output_path(
                    output_dir, filename=filename,
                    max_filename_length=config.max_filename_length,
                )
            except ValueError as e:
                return DownloadResult(url=full_url, success=False, error=str(e))

            output_path.parent.mkdir(parents=True, exist_ok=True)

            if output_path.exists():
                stem = output_path.stem
                suffix = output_path.suffix
                counter = 2
                while output_path.exists():
                    output_path = output_path.parent / f"{stem}_{counter}{suffix}"
                    counter += 1

            chunks: list[bytes] = []
            first_chunk = True
            async for chunk in resp.content.iter_chunked(8192):
                if chunk:
                    if first_chunk:
                        first_chunk = False
                        if require_pdf and not chunk.startswith(b"%PDF"):
                            return DownloadResult(
                                url=full_url, success=False,
                                error="Niet een geldig PDF-bestand",
                            )
                    chunks.append(chunk)

            if not chunks:
                return DownloadResult(url=full_url, success=False, error="Lege response")

            if config.content_filter:
                from mandaat_filter import is_relevant_bytes
                content = b"".join(chunks)
                loop = asyncio.get_running_loop()
                relevant, _ = await loop.run_in_executor(None, is_relevant_bytes, content)
                if not relevant:
                    return DownloadResult(
                        url=full_url, success=False, filtered=True,
                        error="Gefilterd: geen mandaatrelevante inhoud",
                    )
                chunks = [content]

            temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
            try:
                with open(temp_path, "wb") as f:
                    for chunk in chunks:
                        f.write(chunk)
                temp_path.replace(output_path)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            return DownloadResult(url=full_url, success=True, path=output_path)

    except aiohttp.ClientError as e:
        return DownloadResult(
            url=full_url, success=False,
            error=f"Request error: {type(e).__name__}: {e}",
        )
    except Exception as e:
        logger.exception("Onverwachte fout bij download %s", full_url)
        return DownloadResult(
            url=full_url, success=False,
            error=f"Onverwachte fout: {type(e).__name__}: {e}",
        )


async def async_download_documents_parallel(
    session: aiohttp.ClientSession,
    config: ScraperConfig,
    documents: list[dict],
    output_dir: Path,
    require_pdf: bool = True,
    progress_callback: Callable[[DownloadResult], None] | None = None,
) -> list[DownloadResult]:
    """
    Download meerdere documenten concurrent via asyncio.gather.

    Semaphore begrenst gelijktijdige downloads op config.max_parallel_downloads.
    """
    if not documents:
        return []

    sem = asyncio.Semaphore(config.max_parallel_downloads)

    async def _one(doc: dict) -> DownloadResult:
        async with sem:
            result = await async_download_document(
                session, config, doc["url"], output_dir, doc.get("naam", ""), require_pdf,
            )
            if progress_callback:
                progress_callback(result)
            return result

    return list(await asyncio.gather(*[_one(doc) for doc in documents]))


# ---------------------------------------------------------------------------
# Pagina parsing helpers
# ---------------------------------------------------------------------------

def extract_document_links(
    html: str,
    base_url: str,
    link_pattern: str = "/document/",
) -> list[dict]:
    """
    Extraheer document-links uit HTML.

    Args:
        html: HTML content
        base_url: Basis-URL voor relatieve links
        link_pattern: Patroon om te matchen in href

    Returns:
        Lijst van {"url": str, "naam": str} dicts
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    documents = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if link_pattern not in href:
            continue

        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        naam = link.get_text(strip=True) or href.split("/")[-1]
        documents.append({"url": full_url, "naam": naam})

    return documents


# ---------------------------------------------------------------------------
# Maand-berekening helpers
# ---------------------------------------------------------------------------

def berekenen_start_maand(maanden_terug: int) -> tuple[int, int]:
    """
    Bereken de start-maand en jaar voor een periode.

    Args:
        maanden_terug: Aantal maanden terug te gaan

    Returns:
        (jaar, maand) tuple
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    start = datetime.now() - relativedelta(months=maanden_terug)
    return start.year, start.month


def maand_range(
    start_jaar: int,
    start_maand: int,
    eind_jaar: int | None = None,
    eind_maand: int | None = None,
) -> list[tuple[int, int]]:
    """
    Genereer een lijst van (jaar, maand) tuples.

    Args:
        start_jaar: Startjaar
        start_maand: Startmaand (1-12)
        eind_jaar: Eindjaar (standaard: huidig jaar)
        eind_maand: Eindmaand (standaard: huidige maand)

    Returns:
        Lijst van (jaar, maand) tuples, oplopend
    """
    from datetime import datetime

    if eind_jaar is None:
        eind_jaar = datetime.now().year
    if eind_maand is None:
        eind_maand = datetime.now().month

    result = []
    jaar, maand = start_jaar, start_maand

    while (jaar, maand) <= (eind_jaar, eind_maand):
        result.append((jaar, maand))
        maand += 1
        if maand > 12:
            maand = 1
            jaar += 1

    return result


# ---------------------------------------------------------------------------
# Utility functies
# ---------------------------------------------------------------------------

def print_summary(
    results: list[DownloadResult],
    naam: str = "Downloads",
) -> None:
    """Print een samenvatting van download-resultaten."""
    total = len(results)
    success = sum(1 for r in results if r.success and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(1 for r in results if not r.success)

    logger.info(
        "%s: %d totaal, %d nieuw, %d overgeslagen, %d mislukt",
        naam, total, success, skipped, failed,
    )

    if failed > 0:
        for r in results:
            if not r.success:
                logger.warning("  Mislukt: %s — %s", r.url, r.error)
