import logging

from ..config import LLM_PROVIDER, LLM_TAGGING, UPSTAGE_API_KEY
from .static_ai import classifier as _static_classifier
from .static_ai import synthesizer as _static_synthesizer
from .static_ai import tagger as _static_tagger

log = logging.getLogger("sokmaeum.ai")

# 태깅(P1)은 별도 스위치 — 동기 호출이 저장 UX를 막으므로 기본 static
if LLM_TAGGING == "upstage" and UPSTAGE_API_KEY:
    from .upstage_ai import UpstageTagger

    tagger = UpstageTagger()
else:
    tagger = _static_tagger

if LLM_PROVIDER == "upstage" and UPSTAGE_API_KEY:
    from .upstage_ai import UpstageClassifier, UpstageSynthesizer

    classifier, synthesizer = UpstageClassifier(), UpstageSynthesizer()
    AI_MODE = "upstage"
else:
    if LLM_PROVIDER == "upstage" and not UPSTAGE_API_KEY:
        log.warning("LLM_PROVIDER=upstage 인데 UPSTAGE_API_KEY 미설정 — static으로 폴백")
    classifier, synthesizer = _static_classifier, _static_synthesizer
    AI_MODE = "static"
