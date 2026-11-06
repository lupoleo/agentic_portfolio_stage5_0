from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import logging
import re
from typing import Any, Callable, Iterator

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderResponseError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence


NewsLoader = Callable[[str], Any]
# AI-8C.2-R4: query -> {"news": [...], "quotes": [...]} (yfinance Search).
SearchLoader = Callable[[str], dict[str, Any]]

NEWS_SELECTION_POLICY_VERSION = "yahoo-news-selection-v3-multichannel"

# Legal-form tokens removed from company names before searching/matching.
_LEGAL_SUFFIXES = (
    "aktiengesellschaft", "kommanditgesellschaft auf aktien", "& co. kgaa",
    "& co kgaa", "kgaa", "s.p.a.", "s.p.a", "spa", "s.a.", "s.a", "sa", "ag",
    "se", "n.v.", "nv", "plc", "inc.", "inc", "corporation", "corp.", "corp",
    "co.", "ltd.", "ltd", "limited", "holding", "holdings", "adr", "& co.",
    "& co", "rg-a", "rg-b", "rg-c", "cl a", "cl b", "class a", "class b",
)


def normalize_company_name(name: str | None) -> str | None:
    """Company name as used in headlines: no legal form, no share class."""
    if not name:
        return None
    text = re.sub(r"\([^)]*\)", " ", str(name))
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    # Yahoo short names are padded and end with a share-class letter
    # ("SAP SE                        I", "BAYERISCHE MOTOREN WERKE AG   S").
    text = re.sub(r"\s+[A-Z]$", "", text)
    if text.isupper() and len(text) > 4:
        # "UNICREDIT" -> "Unicredit": Yahoo search ranks title case better.
        text = " ".join(
            word if len(word) <= 3 else word.capitalize() for word in text.split(" ")
        )
    changed = True
    while changed and text:
        changed = False
        lowered = text.casefold()
        for suffix in _LEGAL_SUFFIXES:
            if lowered.endswith(" " + suffix) or lowered == suffix:
                text = text[: len(text) - len(suffix)].rstrip(" ,.-&")
                changed = True
                break
    text = re.sub(r"\s+", " ", text).strip(" ,.-&")
    return text if len(text) >= 3 else None


def mentions(text: str, aliases: list[str]) -> bool:
    """Whole-word, case-insensitive mention of any alias."""
    for alias in aliases:
        alias = (alias or "").strip()
        if len(alias) < 2:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


class _ErrorCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _capture_yfinance_errors() -> Iterator[_ErrorCapture]:
    """yfinance logs a failed news request and returns an empty list."""
    handler = _ErrorCapture()
    logger = logging.getLogger("yfinance")
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


class YahooNewsEvidenceProvider(EvidenceProvider):
    """News/event evidence using Yahoo Finance's public ticker news feed.

    This provider only retrieves and normalizes evidence. It performs no
    LLM inference and does not decide whether an item is bullish or bearish.
    """

    def __init__(
        self,
        news_loader: NewsLoader | None = None,
        *,
        search_loader: SearchLoader | None = None,
        default_lookback_days: int = 30,
    ) -> None:
        self._news_loader = news_loader or self._default_news_loader
        # AI-8C.2-R4: an injected ticker loader alone keeps the historical
        # single-channel behaviour (tests, replays). The default provider
        # adds the Yahoo search channels.
        if search_loader is not None:
            self._search_loader: SearchLoader | None = search_loader
        elif news_loader is None:
            self._search_loader = self._default_search_loader
        else:
            self._search_loader = None
        self.default_lookback_days = default_lookback_days

    @property
    def provider_name(self) -> str:
        return "YAHOO_NEWS"

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = datetime.now(timezone.utc)

        normalized, channel_report, item_channels = self._collect(request)
        if not normalized:
            attempted = {
                name: state for name, state in channel_report.items()
                if name != "SEARCH_ALIASES"
            }
            failed = [name for name, state in attempted.items() if state.startswith("FAILED")]
            if failed and len(failed) == len(attempted):
                raise EvidenceProviderResponseError(
                    f"Yahoo news channels down for {request.ticker}: "
                    + "; ".join(f"{name}={channel_report[name]}" for name in failed)
                )
            warnings = ["No usable news/event evidence returned"]
            warnings.extend(
                f"News channel {name} {state}" for name, state in channel_report.items()
                if state.startswith("FAILED")
            )
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                warnings=warnings,
                fetched_at=fetched_at,
                metadata={
                    "selection_policy_version": NEWS_SELECTION_POLICY_VERSION,
                    "news_channels": channel_report,
                },
            )

        cutoff = None
        if request.as_of is not None:
            as_of = self._ensure_utc(request.as_of)
            from datetime import timedelta
            cutoff = as_of - timedelta(days=self.default_lookback_days)

        # AI-8C.3b.8: Yahoo feed order is not a stable selection contract.
        # Normalize the complete candidate set, apply the as-of window, then
        # canonicalize order before deduplication and max_items truncation.
        candidates: list[dict[str, Any]] = []
        for raw in normalized:
            parsed = self._parse_item(raw, request.ticker, fetched_at)
            if parsed is None:
                continue
            parsed["channel"] = item_channels.get(id(raw), "TICKER_FEED")

            published_at = parsed["published_at"]
            if request.as_of is not None:
                as_of = self._ensure_utc(request.as_of)
                if published_at is not None and published_at > as_of:
                    continue
                if cutoff is not None and published_at is not None and published_at < cutoff:
                    continue
            candidates.append(parsed)

        candidates.sort(key=self._canonical_sort_key)

        output: list[EvidenceItem] = []
        seen: set[str] = set()
        for parsed in candidates:
            dedup_key = self._dedup_key(parsed)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            output.append(
                self._build_item(
                    ticker=request.ticker,
                    parsed=parsed,
                    fetched_at=fetched_at,
                )
            )
            if len(output) >= request.max_items:
                break

        if not output:
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                warnings=["News was returned but no usable items passed normalization/filtering"],
                fetched_at=fetched_at,
                metadata={
                    "selection_policy_version": NEWS_SELECTION_POLICY_VERSION,
                    "news_channels": channel_report,
                    "relevant_outside_window": len(normalized),
                },
            )

        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS,
            items=output,
            fetched_at=fetched_at,
            metadata={
                "lookback_days": self.default_lookback_days,
                "selection_policy_version": NEWS_SELECTION_POLICY_VERSION,
                "normalized_candidate_count": len(normalized),
                "eligible_candidate_count": len(candidates),
                "news_channels": channel_report,
                "selected_evidence_ids": [
                    item.evidence.evidence_id for item in output
                ],
            },
        )

    def _collect(
        self, request: EvidenceRequest
    ) -> tuple[list[dict[str, Any]], dict[str, str], dict[int, str]]:
        """Gather raw items from the ticker feed, then the search channels.

        Returns the raw items, a per-channel state and the channel of each
        raw item (keyed by object identity).
        """
        report: dict[str, str] = {}
        channels: dict[int, str] = {}

        # 1. Ticker feed: authoritative when it works.
        try:
            with _capture_yfinance_errors() as captured:
                raw = self._news_loader(request.ticker)
            feed = self._normalize_collection(raw)
            if captured.messages and not feed:
                report["TICKER_FEED"] = "FAILED:" + captured.messages[0][:120]
            else:
                report["TICKER_FEED"] = f"OK:{len(feed)}"
        except EvidenceProviderResponseError:
            raise
        except Exception as exc:
            if self._search_loader is None:
                raise EvidenceProviderResponseError(
                    f"Yahoo news retrieval failed for {request.ticker}: {exc}"
                ) from exc
            feed = []
            report["TICKER_FEED"] = f"FAILED:{type(exc).__name__}"
        if feed or self._search_loader is None:
            for item in feed:
                channels[id(item)] = "TICKER_FEED"
            return feed, report, channels

        # 2. Search by symbol: news for US listings, and the company names.
        ticker = request.ticker.strip().upper()
        base_symbol = ticker.split(".", 1)[0]
        names: list[str] = []
        for value in (request.metadata or {}).get("company_names", []) or []:
            normalized = normalize_company_name(value)
            if normalized and normalized.casefold() not in {n.casefold() for n in names}:
                names.append(normalized)
        searched: list[dict[str, Any]] = []
        try:
            payload = self._search_loader(ticker) or {}
            symbol_news = self._normalize_collection(payload.get("news"))
            report["SEARCH_SYMBOL"] = f"OK:{len(symbol_news)}"
            for item in symbol_news:
                channels[id(item)] = "SEARCH_SYMBOL"
            searched.extend(symbol_news)
            for quote in payload.get("quotes") or []:
                if not isinstance(quote, dict) or str(quote.get("symbol", "")).upper() != ticker:
                    continue
                for key in ("shortname", "longname"):
                    normalized = normalize_company_name(quote.get(key))
                    if normalized and normalized.casefold() not in {n.casefold() for n in names}:
                        names.append(normalized)
        except Exception as exc:
            report["SEARCH_SYMBOL"] = f"FAILED:{type(exc).__name__}"

        aliases = [ticker, base_symbol, *names]
        relevant = [item for item in searched if self._is_relevant(item, aliases)]

        # 3. Search by company name, when the symbol found nothing relevant.
        # Full legal names often return no news ("Bayerische Motoren Werke"),
        # so a listing outside the US also tries its base symbol ("BMW").
        queries = list(names[:1])
        if (
            "." in ticker
            and len(base_symbol) >= 3
            and base_symbol.isalpha()
            and base_symbol.casefold() not in {q.casefold() for q in queries}
        ):
            queries.append(base_symbol)
        for index, query in enumerate(queries):
            if relevant:
                break
            key = "SEARCH_NAME" if index == 0 and names else "SEARCH_BASE_SYMBOL"
            try:
                payload = self._search_loader(query) or {}
                found = self._normalize_collection(payload.get("news"))
                report[key] = f"OK:{len(found)}"
                for item in found:
                    channels[id(item)] = key
                relevant.extend(
                    item for item in found if self._is_relevant(item, aliases)
                )
            except Exception as exc:
                report[key] = f"FAILED:{type(exc).__name__}"
        report["SEARCH_ALIASES"] = "|".join(aliases)
        return relevant, report, channels

    def _is_relevant(self, raw: dict[str, Any], aliases: list[str]) -> bool:
        content = raw.get("content")
        data = content if isinstance(content, dict) else raw
        text = " ".join(
            value for value in (
                self._first_text(data, "title"),
                self._first_text(data, "summary", "description", "snippet"),
            ) if value
        )
        return bool(text) and mentions(text, aliases)

    @staticmethod
    def _default_search_loader(query: str) -> dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise EvidenceProviderResponseError(
                "yfinance is required for YahooNewsEvidenceProvider"
            ) from exc
        search = yf.Search(query, max_results=8, news_count=20, raise_errors=True)
        return {"news": list(search.news or []), "quotes": list(search.quotes or [])}

    @staticmethod
    def _default_news_loader(ticker: str) -> Any:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise EvidenceProviderResponseError(
                "yfinance is required for YahooNewsEvidenceProvider"
            ) from exc
        return yf.Ticker(ticker).news

    @staticmethod
    def _normalize_collection(raw_items: Any) -> list[dict[str, Any]]:
        if raw_items is None:
            return []
        if isinstance(raw_items, list):
            return [x for x in raw_items if isinstance(x, dict)]
        raise EvidenceProviderResponseError(
            "Unsupported Yahoo news result type"
        )

    def _parse_item(
        self,
        raw: dict[str, Any],
        ticker: str,
        fetched_at: datetime,
    ) -> dict[str, Any] | None:
        # yfinance has used both flat and nested ("content") news shapes.
        content = raw.get("content")
        data = content if isinstance(content, dict) else raw

        title = self._first_text(data, "title")
        if not title:
            return None

        summary = self._first_text(
            data, "summary", "description", "snippet"
        )

        publisher = self._publisher(data) or self._publisher(raw) or "Yahoo Finance feed"
        url = self._url(data) or self._url(raw)
        published_at = (
            self._published_at(data)
            or self._published_at(raw)
            or fetched_at
        )

        text = f"Headline: {title}"
        if summary:
            text += f". Summary: {summary}"

        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "url": url,
            "published_at": published_at,
            "text": text,
            "ticker": ticker,
        }

    @staticmethod
    def _first_text(data: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _publisher(data: dict[str, Any]) -> str | None:
        for key in ("publisher", "provider", "source"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested in ("displayName", "name"):
                    text = value.get(nested)
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        return None

    @staticmethod
    def _url(data: dict[str, Any]) -> str | None:
        for key in ("link", "url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        canonical = data.get("canonicalUrl")
        if isinstance(canonical, dict):
            value = canonical.get("url")
            if isinstance(value, str) and value.strip():
                return value.strip()

        click = data.get("clickThroughUrl")
        if isinstance(click, dict):
            value = click.get("url")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _published_at(self, data: dict[str, Any]) -> datetime | None:
        for key in ("providerPublishTime", "pubDate", "published_at", "publishedAt"):
            value = data.get(key)
            parsed = self._parse_datetime(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return YahooNewsEvidenceProvider._ensure_utc(value)
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            try:
                return YahooNewsEvidenceProvider._ensure_utc(
                    datetime.fromisoformat(text)
                )
            except ValueError:
                return None
        return None

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _canonical_sort_key(parsed: dict[str, Any]) -> tuple[float, str, str]:
        published_at = parsed["published_at"]
        timestamp = published_at.timestamp() if published_at is not None else 0.0
        return (
            -timestamp,
            parsed["title"].strip().casefold(),
            (parsed["url"] or "").strip().casefold(),
        )

    @staticmethod
    def _dedup_key(parsed: dict[str, Any]) -> str:
        base = "|".join(
            [
                parsed["title"].strip().lower(),
                (parsed["url"] or "").strip().lower(),
            ]
        )
        return sha256(base.encode("utf-8")).hexdigest()

    def _build_item(
        self,
        *,
        ticker: str,
        parsed: dict[str, Any],
        fetched_at: datetime,
    ) -> EvidenceItem:
        published_at = parsed["published_at"]
        digest_input = "|".join(
            [
                ticker,
                published_at.isoformat(),
                parsed["title"],
                parsed["url"] or "",
            ]
        )
        digest = sha256(digest_input.encode("utf-8")).hexdigest()[:12]

        evidence_id = f"EVID-YNEWS-{ticker}-{digest}"
        source_id = f"SRC-YNEWS-{ticker}-{digest}"

        source = EvidenceSource(
            source_id=source_id,
            provider=self.provider_name,
            source_type="NEWS_EVENT",
            source_name=parsed["publisher"],
            source_url=parsed["url"],
            retrieved_at=fetched_at,
            published_at=published_at,
            metadata={
                "ticker": ticker,
                "headline": parsed["title"],
                "news_channel": parsed.get("channel", "TICKER_FEED"),
            },
        )

        evidence = ResearchEvidence(
            evidence_id=evidence_id,
            source_type="NEWS_EVENT",
            text=parsed["text"],
            published_at=published_at,
            metadata={
                "provider": self.provider_name,
                "source_id": source_id,
                "publisher": parsed["publisher"],
                "url": parsed["url"],
                "news_channel": parsed.get("channel", "TICKER_FEED"),
            },
        )

        return EvidenceItem(
            evidence=evidence,
            source=source,
            kind=EvidenceKind.NEWS,
            ticker=ticker,
            metadata={"headline": parsed["title"]},
        )
