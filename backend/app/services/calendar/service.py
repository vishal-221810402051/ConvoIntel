"""Canonical deterministic calendar recommendation publication service."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.models.calendar_recommendation import (
    CALENDAR_RECOMMENDATION_GENERATOR_VERSION,
    CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH,
    CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH,
    CalendarGeneratorMetadata,
    CalendarInputMetadata,
    CalendarOutputMetadata,
    CalendarProcessingMetadata,
    CalendarRecommendationArtifact,
    CalendarRecommendationMetadata,
    CalendarRecommendationResult,
    calendar_recommendation_counts,
)
from backend.app.models.intelligence import (
    INTELLIGENCE_JSON_RELATIVE_PATH,
    INTELLIGENCE_METADATA_RELATIVE_PATH,
    DecisionIntelligenceMetadata,
    DecisionIntelligenceResult,
)
from backend.app.models.temporal import (
    TEMPORAL_JSON_RELATIVE_PATH,
    TEMPORAL_METADATA_RELATIVE_PATH,
    TemporalIntelligenceResult,
    TemporalMetadata,
    TemporalReference,
)
from backend.app.services.calendar.errors import (
    CalendarInputIntegrityError,
    CalendarInputNotFoundError,
    CalendarMetadataWriteError,
    CalendarPolicyError,
    CalendarPublicationError,
    CalendarRecommendationError,
    CalendarStateError,
)
from backend.app.services.calendar.policy import build_calendar_recommendations
from backend.app.services.intelligence.errors import (
    DecisionIntelligenceError,
    IntelligenceInputNotFoundError,
    IntelligenceStateError,
)
from backend.app.services.intelligence.service import DecisionIntelligenceService
from backend.app.services.temporal.errors import (
    TemporalInputNotFoundError,
    TemporalIntelligenceError,
    TemporalStateError,
)
from backend.app.services.temporal.service import TemporalIntelligenceService

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024

Clock = Callable[[], dt.datetime]


@dataclass(frozen=True)
class _CalendarContext:
    meeting_id: str
    meeting_dir: Path
    phase6_result: DecisionIntelligenceResult
    phase7_result: TemporalIntelligenceResult
    intelligence_json_path: Path
    intelligence_json_size_bytes: int
    intelligence_json_sha256: str
    intelligence_metadata_path: Path
    intelligence_metadata_size_bytes: int
    intelligence_metadata_sha256: str
    temporal_json_path: Path
    temporal_json_size_bytes: int
    temporal_json_sha256: str
    temporal_metadata_path: Path
    temporal_metadata_size_bytes: int
    temporal_metadata_sha256: str


class _Phase6Guard:
    def analyze(self, request: Any) -> Any:
        raise CalendarStateError("Completed Phase 6 intelligence is required.")


class _Phase7Guard:
    def extract(self, request: Any) -> Any:
        raise CalendarStateError("Completed Phase 7 temporal intelligence is required.")


class CalendarRecommendationService:
    """Publish deterministic calendar recommendations for completed meetings."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._clock = clock or _utc_clock

    def generate_calendar_recommendations(
        self,
        meeting_id: str,
    ) -> CalendarRecommendationResult:
        """Generate or reuse Phase 8 calendar recommendations for a meeting."""

        logger.info("Starting calendar recommendation generation for %s", meeting_id)
        context = self._load_context(meeting_id)
        logger.info(
            "Phase 7 integrity verified for %s with %s temporal items and %s gaps",
            context.meeting_id,
            len(context.phase7_result.temporal_intelligence.items),
            len(context.phase7_result.temporal_intelligence.gaps),
        )

        recommendations_path = (
            context.meeting_dir / CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH
        )
        metadata_path = (
            context.meeting_dir / CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH
        )

        existing = self._reuse_existing_if_valid(
            context,
            recommendations_path,
            metadata_path,
        )
        if existing is not None:
            logger.info("Reusing calendar recommendations for %s", context.meeting_id)
            return existing

        staging_dir = (
            context.meeting_dir
            / ".staging"
            / f"calendar_recommendations_{uuid.uuid4().hex}"
        )

        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_recommendations_path = staging_dir / "recommendations.json"
            staged_metadata_path = staging_dir / "calendar_recommendations.json"

            artifact = self._build_artifact(context)
            self._write_json_atomically(
                artifact,
                staged_recommendations_path,
                CalendarPublicationError,
                "Calendar recommendations JSON could not be written.",
            )
            recommendations_size, recommendations_sha = self._inspect_file(
                staged_recommendations_path,
                CalendarPublicationError,
            )
            metadata = self._build_metadata(
                context,
                artifact,
                recommendations_size,
                recommendations_sha,
            )
            self._write_json_atomically(
                metadata,
                staged_metadata_path,
                CalendarMetadataWriteError,
                "Calendar recommendation metadata could not be written.",
            )
            self._publish_artifacts(
                staged_recommendations_path,
                recommendations_path,
                staged_metadata_path,
                metadata_path,
            )
            logger.info(
                "Completed calendar recommendations for %s: %s recommendations, %s exclusions",
                context.meeting_id,
                len(artifact.recommendations),
                len(artifact.exclusions),
            )
            return CalendarRecommendationResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                recommendations_json_path=recommendations_path.resolve(strict=False),
                recommendations_metadata_path=metadata_path.resolve(strict=False),
                recommendations=artifact,
                metadata=metadata,
                reused_existing=False,
            )
        except CalendarRecommendationError:
            logger.info("Calendar recommendations failed for %s", meeting_id)
            raise
        except OSError as exc:
            logger.info("Calendar recommendations failed for %s", meeting_id)
            raise CalendarPublicationError(
                "Calendar recommendation artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_context(self, meeting_id: str) -> _CalendarContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise CalendarInputIntegrityError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise CalendarInputNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise CalendarInputNotFoundError("Meeting package was not found.")

        intelligence_path = meeting_dir / INTELLIGENCE_JSON_RELATIVE_PATH
        intelligence_metadata_path = meeting_dir / INTELLIGENCE_METADATA_RELATIVE_PATH
        temporal_path = meeting_dir / TEMPORAL_JSON_RELATIVE_PATH
        temporal_metadata_path = meeting_dir / TEMPORAL_METADATA_RELATIVE_PATH
        if intelligence_path.exists() != intelligence_metadata_path.exists():
            raise CalendarStateError("Meeting package contains partial Phase 6 state.")
        if temporal_path.exists() != temporal_metadata_path.exists():
            raise CalendarStateError("Meeting package contains partial Phase 7 state.")
        if not intelligence_path.exists():
            raise CalendarInputNotFoundError("Phase 6 intelligence is required.")
        if not temporal_path.exists():
            raise CalendarInputNotFoundError("Phase 7 temporal intelligence is required.")

        temporal_metadata = self._load_model(
            temporal_metadata_path,
            TemporalMetadata,
            CalendarInputNotFoundError,
            "Temporal metadata was not found.",
            "Temporal metadata is invalid for calendar recommendations.",
        )
        reference = temporal_metadata.input.temporal_reference

        try:
            phase6_result = DecisionIntelligenceService(
                self._settings,
                provider=_Phase6Guard(),
            ).analyze_meeting(meeting_id)
            phase7_result = self._validate_temporal(meeting_id, reference)
        except (IntelligenceInputNotFoundError, TemporalInputNotFoundError) as exc:
            raise CalendarInputNotFoundError("Phase 1-7 input chain is incomplete.") from exc
        except (IntelligenceStateError, TemporalStateError) as exc:
            raise CalendarStateError("Phase 1-7 input chain state is invalid.") from exc
        except (DecisionIntelligenceError, TemporalIntelligenceError) as exc:
            raise CalendarInputIntegrityError(
                "Phase 1-7 input chain failed integrity validation."
            ) from exc

        intelligence_size, intelligence_sha = self._inspect_file(
            intelligence_path,
            CalendarInputIntegrityError,
        )
        intelligence_metadata_size, intelligence_metadata_sha = self._inspect_file(
            intelligence_metadata_path,
            CalendarInputIntegrityError,
        )
        temporal_size, temporal_sha = self._inspect_file(
            temporal_path,
            CalendarInputIntegrityError,
        )
        temporal_metadata_size, temporal_metadata_sha = self._inspect_file(
            temporal_metadata_path,
            CalendarInputIntegrityError,
        )
        if intelligence_sha != phase6_result.metadata.output.intelligence_sha256:
            raise CalendarInputIntegrityError(
                "Decision intelligence checksum does not match metadata."
            )
        if temporal_sha != phase7_result.metadata.output.temporal_sha256:
            raise CalendarInputIntegrityError(
                "Temporal intelligence checksum does not match metadata."
            )
        if phase7_result.temporal_intelligence.source_intelligence_sha256 != intelligence_sha:
            raise CalendarInputIntegrityError(
                "Temporal source intelligence checksum does not match."
            )
        if (
            phase7_result.temporal_intelligence.source_intelligence_metadata_sha256
            != intelligence_metadata_sha
        ):
            raise CalendarInputIntegrityError(
                "Temporal source intelligence metadata checksum does not match."
            )

        return _CalendarContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            phase6_result=phase6_result,
            phase7_result=phase7_result,
            intelligence_json_path=intelligence_path,
            intelligence_json_size_bytes=intelligence_size,
            intelligence_json_sha256=intelligence_sha,
            intelligence_metadata_path=intelligence_metadata_path,
            intelligence_metadata_size_bytes=intelligence_metadata_size,
            intelligence_metadata_sha256=intelligence_metadata_sha,
            temporal_json_path=temporal_path,
            temporal_json_size_bytes=temporal_size,
            temporal_json_sha256=temporal_sha,
            temporal_metadata_path=temporal_metadata_path,
            temporal_metadata_size_bytes=temporal_metadata_size,
            temporal_metadata_sha256=temporal_metadata_sha,
        )

    def _validate_temporal(
        self,
        meeting_id: str,
        reference: TemporalReference | None,
    ) -> TemporalIntelligenceResult:
        service = TemporalIntelligenceService(
            self._settings,
            provider=_Phase7Guard(),
        )
        if reference is None:
            return service.extract_temporal_intelligence(meeting_id)
        return service.extract_temporal_intelligence(
            meeting_id,
            reference_datetime=reference.reference_datetime_local,
            timezone_name=reference.timezone_name,
        )

    def _build_artifact(
        self,
        context: _CalendarContext,
    ) -> CalendarRecommendationArtifact:
        try:
            policy = build_calendar_recommendations(
                context.phase6_result.intelligence,
                context.phase7_result.temporal_intelligence,
            )
        except CalendarPolicyError:
            raise
        except ValueError as exc:
            raise CalendarPolicyError(
                "Calendar recommendation policy produced invalid output."
            ) from exc
        logger.info(
            "Calendar policy grouped %s contexts for %s",
            policy.candidate_context_count,
            context.meeting_id,
        )
        return CalendarRecommendationArtifact(
            meeting_id=context.meeting_id,
            source_intelligence_sha256=context.intelligence_json_sha256,
            source_intelligence_metadata_sha256=context.intelligence_metadata_sha256,
            source_temporal_sha256=context.temporal_json_sha256,
            source_temporal_metadata_sha256=context.temporal_metadata_sha256,
            recommendations=policy.recommendations,
            exclusions=policy.exclusions,
        )

    def _build_metadata(
        self,
        context: _CalendarContext,
        artifact: CalendarRecommendationArtifact,
        recommendations_size: int,
        recommendations_sha: str,
    ) -> CalendarRecommendationMetadata:
        counts = calendar_recommendation_counts(artifact)
        return CalendarRecommendationMetadata(
            meeting_id=context.meeting_id,
            created_at_utc=self._utc_now(),
            generator=CalendarGeneratorMetadata(),
            input=CalendarInputMetadata(
                intelligence_size_bytes=context.intelligence_json_size_bytes,
                intelligence_sha256=context.intelligence_json_sha256,
                intelligence_metadata_size_bytes=(
                    context.intelligence_metadata_size_bytes
                ),
                intelligence_metadata_sha256=context.intelligence_metadata_sha256,
                temporal_size_bytes=context.temporal_json_size_bytes,
                temporal_sha256=context.temporal_json_sha256,
                temporal_metadata_size_bytes=context.temporal_metadata_size_bytes,
                temporal_metadata_sha256=context.temporal_metadata_sha256,
                temporal_item_count=len(
                    context.phase7_result.temporal_intelligence.items
                ),
                temporal_gap_count=len(
                    context.phase7_result.temporal_intelligence.gaps
                ),
            ),
            output=CalendarOutputMetadata(
                recommendations_size_bytes=recommendations_size,
                recommendations_sha256=recommendations_sha,
                **counts,
            ),
            processing=CalendarProcessingMetadata(),
        )

    def _reuse_existing_if_valid(
        self,
        context: _CalendarContext,
        recommendations_path: Path,
        metadata_path: Path,
    ) -> CalendarRecommendationResult | None:
        recommendations_exists = recommendations_path.exists()
        metadata_exists = metadata_path.exists()
        if recommendations_exists != metadata_exists:
            raise CalendarStateError(
                "Meeting package contains partial calendar recommendation state."
            )
        if not recommendations_exists:
            return None

        artifact = self._load_model(
            recommendations_path,
            CalendarRecommendationArtifact,
            CalendarStateError,
            "Calendar recommendations JSON was not found.",
            "Calendar recommendations JSON is invalid.",
        )
        metadata = self._load_model(
            metadata_path,
            CalendarRecommendationMetadata,
            CalendarStateError,
            "Calendar recommendation metadata was not found.",
            "Calendar recommendation metadata is invalid.",
        )
        self._validate_existing(
            context,
            artifact,
            metadata,
            recommendations_path,
        )
        return CalendarRecommendationResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            recommendations_json_path=recommendations_path.resolve(strict=False),
            recommendations_metadata_path=metadata_path.resolve(strict=False),
            recommendations=artifact,
            metadata=metadata,
            reused_existing=True,
        )

    def _validate_existing(
        self,
        context: _CalendarContext,
        artifact: CalendarRecommendationArtifact,
        metadata: CalendarRecommendationMetadata,
        recommendations_path: Path,
    ) -> None:
        if artifact.meeting_id != context.meeting_id or metadata.meeting_id != context.meeting_id:
            raise CalendarStateError("Calendar recommendation meeting ID does not match.")
        if artifact.generator_version != CALENDAR_RECOMMENDATION_GENERATOR_VERSION:
            raise CalendarStateError("Calendar recommendation generator version differs.")
        if metadata.generator.version != CALENDAR_RECOMMENDATION_GENERATOR_VERSION:
            raise CalendarStateError("Calendar metadata generator version differs.")
        if metadata.generator.mode != "deterministic_local":
            raise CalendarStateError("Calendar metadata generator mode differs.")
        if metadata.generator.network_access is not False:
            raise CalendarStateError("Calendar metadata network-access flag differs.")
        if metadata.generator.provider_request_count != 0:
            raise CalendarStateError("Calendar metadata local generator count differs.")
        if artifact.source_intelligence_sha256 != context.intelligence_json_sha256:
            raise CalendarStateError("Calendar source intelligence checksum differs.")
        if (
            artifact.source_intelligence_metadata_sha256
            != context.intelligence_metadata_sha256
        ):
            raise CalendarStateError(
                "Calendar source intelligence metadata checksum differs."
            )
        if artifact.source_temporal_sha256 != context.temporal_json_sha256:
            raise CalendarStateError("Calendar source temporal checksum differs.")
        if artifact.source_temporal_metadata_sha256 != context.temporal_metadata_sha256:
            raise CalendarStateError("Calendar source temporal metadata checksum differs.")
        if metadata.input.intelligence_size_bytes != context.intelligence_json_size_bytes:
            raise CalendarStateError("Calendar input intelligence size differs.")
        if metadata.input.intelligence_sha256 != context.intelligence_json_sha256:
            raise CalendarStateError("Calendar input intelligence checksum differs.")
        if (
            metadata.input.intelligence_metadata_sha256
            != context.intelligence_metadata_sha256
        ):
            raise CalendarStateError(
                "Calendar input intelligence metadata checksum differs."
            )
        if metadata.input.temporal_size_bytes != context.temporal_json_size_bytes:
            raise CalendarStateError("Calendar input temporal size differs.")
        if metadata.input.temporal_sha256 != context.temporal_json_sha256:
            raise CalendarStateError("Calendar input temporal checksum differs.")
        if metadata.input.temporal_metadata_sha256 != context.temporal_metadata_sha256:
            raise CalendarStateError("Calendar input temporal metadata checksum differs.")
        if metadata.input.temporal_item_count != len(context.phase7_result.temporal_intelligence.items):
            raise CalendarStateError("Calendar input temporal count differs.")
        if metadata.input.temporal_gap_count != len(context.phase7_result.temporal_intelligence.gaps):
            raise CalendarStateError("Calendar input temporal gap count differs.")

        recommendations_size, recommendations_sha = self._inspect_file(
            recommendations_path,
            CalendarStateError,
        )
        if metadata.output.recommendations_size_bytes != recommendations_size:
            raise CalendarStateError("Calendar recommendation size differs.")
        if metadata.output.recommendations_sha256 != recommendations_sha:
            raise CalendarStateError("Calendar recommendation checksum differs.")
        if metadata.output.model_dump() != CalendarOutputMetadata(
            recommendations_size_bytes=recommendations_size,
            recommendations_sha256=recommendations_sha,
            **calendar_recommendation_counts(artifact),
        ).model_dump():
            raise CalendarStateError("Calendar recommendation counts differ.")

        expected = self._build_artifact(context)
        if artifact.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise CalendarStateError("Calendar recommendation mapping differs.")

    def _load_model(
        self,
        path: Path,
        model_type: Any,
        missing_error: type[CalendarRecommendationError],
        missing_message: str,
        invalid_message: str,
    ) -> Any:
        if not path.exists():
            raise missing_error(missing_message)
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            if missing_error is CalendarStateError:
                raise CalendarStateError(invalid_message) from exc
            raise CalendarInputIntegrityError(invalid_message) from exc

    def _write_json_atomically(
        self,
        model: CalendarRecommendationArtifact | CalendarRecommendationMetadata,
        path: Path,
        error_type: type[CalendarRecommendationError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[CalendarRecommendationError],
        message: str,
    ) -> None:
        temp_path = path.with_name(f".tmp_{uuid.uuid4().hex[:12]}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary calendar artifact")
            raise error_type(message) from exc

    def _publish_artifacts(
        self,
        staged_recommendations_path: Path,
        recommendations_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published: list[Path] = []
        try:
            recommendations_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if recommendations_path.exists() or metadata_path.exists():
                raise CalendarStateError(
                    "Meeting package contains inconsistent calendar recommendation state."
                )
            os.replace(staged_recommendations_path, recommendations_path)
            published.append(recommendations_path)
            if metadata_path.exists():
                raise CalendarStateError(
                    "Meeting package contains inconsistent calendar recommendation state."
                )
            os.replace(staged_metadata_path, metadata_path)
            published.append(metadata_path)
        except CalendarStateError:
            self._remove_published_artifacts(published)
            raise
        except OSError as exc:
            self._remove_published_artifacts(published)
            raise CalendarPublicationError(
                "Calendar recommendation artifacts could not be published."
            ) from exc

    def _remove_published_artifacts(self, paths: list[Path]) -> None:
        logger.info("Attempting calendar recommendation artifact rollback")
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Rollback failed for published calendar artifact")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                logger.info("Rolling back staged calendar artifacts")
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged calendar artifacts")

    def _inspect_file(
        self,
        path: Path,
        error_type: type[CalendarRecommendationError],
    ) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                if error_type is CalendarInputIntegrityError:
                    raise CalendarInputNotFoundError(
                        "Calendar recommendation input artifact is missing."
                    )
                raise error_type("Calendar recommendation artifact is missing.")
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise error_type(
                "Calendar recommendation artifact could not be inspected."
            ) from exc
        if size_bytes <= 0:
            raise error_type("Calendar recommendation artifact is empty.")
        return size_bytes, self._hash_file(path, error_type)

    def _hash_file(
        self,
        path: Path,
        error_type: type[CalendarRecommendationError],
    ) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise error_type("Artifact could not be hashed.") from exc
        return checksum.hexdigest()

    def _utc_now(self) -> dt.datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise CalendarPublicationError("Clock must return a timezone-aware datetime.")
        return value.astimezone(dt.timezone.utc)

    def _is_relative_to(self, child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent.resolve(strict=False))
            return True
        except ValueError:
            return False


def _utc_clock() -> dt.datetime:
    return getattr(dt.datetime, "now")(dt.timezone.utc)
