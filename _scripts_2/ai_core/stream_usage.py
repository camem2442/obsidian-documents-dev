"""Attempt-local observed streaming usage; missing reports never imply zero cost."""
from copy import deepcopy
import logging

_LOG = logging.getLogger(__name__)


class StreamUsageAttempt:
    """Collect cumulative snapshots and attempt one ledger write on finalization."""

    def __init__(self, provider: str, requested_model: str):
        self.provider = provider
        self.requested_model = requested_model
        self._model = None
        self._usage = None
        self._invalid = False
        self._finalized = False
        self._report = None

    def observe(self, parsed):
        if self._finalized:
            return
        if not isinstance(parsed, dict):
            self._invalid = True
            return
        model_key = "modelVersion" if self.provider == "gemini" else "model"
        if model_key in parsed:
            model = parsed[model_key]
            if not isinstance(model, str) or not model.strip() or (self._model is not None and model != self._model):
                self._invalid = True
            else:
                self._model = model
        snapshots = []
        if self.provider == "gemini":
            if "usageMetadata" in parsed:
                snapshots.append(parsed["usageMetadata"])
            fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")
        elif self.provider == "groq":
            if "usage" in parsed:
                snapshots.append(parsed["usage"])
            if "x_groq" in parsed:
                metadata = parsed["x_groq"]
                if metadata is None:
                    pass
                elif not isinstance(metadata, dict):
                    self._invalid = True
                elif "usage" in metadata:
                    snapshots.append(metadata["usage"])
            fields = ("prompt_tokens", "completion_tokens", "total_tokens")
        else:
            self._invalid = True
            return
        normalized = []
        for snapshot in snapshots:
            if snapshot is None:
                continue
            if not isinstance(snapshot, dict) or any(type(snapshot.get(key)) is not int or snapshot[key] < 0 for key in fields):
                self._invalid = True
                continue
            counts = tuple(snapshot[key] for key in fields)
            if counts[2] < counts[0] + counts[1]:
                self._invalid = True
                continue
            normalized.append(counts)
        if len(normalized) > 1 and any(counts != normalized[0] for counts in normalized[1:]):
            self._invalid = True
        if normalized:
            latest = normalized[-1]
            if self._usage is not None and any(new < old for new, old in zip(latest, self._usage)):
                self._invalid = True
            self._usage = latest

    def finalize(self):
        if self._finalized:
            return self.report
        self._finalized = True
        report = {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "model": self._model or self.requested_model,
            "model_source": "returned" if self._model is not None else "requested",
            "status": "invalid" if self._invalid else "missing",
        }
        if self._usage is not None and not self._invalid:
            report.update(zip(("prompt_tokens", "completion_tokens", "total_tokens"), self._usage))
            try:
                from .usage_tracker import record_usage
                recorded = record_usage(self.provider, report["model"], *self._usage)
            except Exception:
                recorded = False
            report["status"] = "recorded" if recorded is True else "persistence_failed"
            if recorded is not True:
                _LOG.error("Stream usage persistence failed; no automatic retry.")
        self._report = report
        return self.report

    @property
    def report(self):
        if self._report is None:
            return {
                "provider": self.provider, "requested_model": self.requested_model,
                "model": self._model or self.requested_model,
                "model_source": "returned" if self._model is not None else "requested",
                "status": "invalid" if self._invalid else "missing",
            }
        return deepcopy(self._report)
