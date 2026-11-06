"""Provider-neutral AI infrastructure for Stage 3."""

from .inference import (
    AIInferenceRecord,
    AIValidationStatus,
    build_inference_record,
)
from .local_provider import (
    LocalProvider,
    LocalProviderConnectionError,
    LocalProviderError,
    LocalProviderResponseError,
    LocalProviderTimeoutError,
    LocalProviderValidationError,
)
from .models import (
    AIModel,
    AIRequest,
    AIResponse,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from .provider import AIModelProvider
from .schemas import FinancialSentimentResult, SentimentLabel

__all__ = [
    "AIInferenceRecord",
    "AIModel",
    "AIModelProvider",
    "AIRequest",
    "AIResponse",
    "AITask",
    "AIValidationStatus",
    "DataSensitivity",
    "FinancialSentimentResult",
    "LocalProvider",
    "LocalProviderConnectionError",
    "LocalProviderError",
    "LocalProviderResponseError",
    "LocalProviderTimeoutError",
    "LocalProviderValidationError",
    "ReasoningMode",
    "ResponseFormat",
    "SentimentLabel",
    "build_inference_record",
]

# AI-5A exports
from .scan_models import (
    CandidateAction,
    CandidateOrigin,
    CatalystType,
    MarketScan,
    MarketScanStatus,
    ScannerType,
    ScanCandidate,
    ScanUniverseType,
    SignalType,
)
# AI-6A exports to append to app/ai/__init__.py

from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
# AI-6C exports to append to app/ai/__init__.py

from app.ai.research_service import (
    RESEARCH_PROMPT_VERSION,
    ResearchEvidence,
    ResearchModelOutput,
    ResearchResult,
    ResearchService,
)
# AI-7A exports — append these lines to app/ai/__init__.py

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderConnectionError,
    EvidenceProviderError,
    EvidenceProviderResponseError,
    EvidenceProviderTimeoutError,
    EvidenceRequest,
    EvidenceSource,
)

from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.analyst_evidence_provider import YahooAnalystEvidenceProvider
from app.ai.fundamental_evidence_provider import YahooFundamentalEvidenceProvider
from app.ai.technical_evidence_adapter import CanonicalTechnicalEvidenceAdapter

from app.ai.news_relevance import (
    NEWS_RELEVANCE_PROMPT_VERSION,
    NewsRelevanceAssessment,
    NewsRelevanceLabel,
    NewsRelevanceModelOutput,
    NewsRelevanceResult,
    NewsRelevanceService,
)

# AI-7C.2 replaces the AI-7C.1 news_relevance implementation.
# Existing imports from app.ai.news_relevance remain valid.
from app.ai.news_relevance import NewsRelevanceMethod

from app.ai.evidence_aggregator import AggregatedEvidence, EvidenceAggregator
