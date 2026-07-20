"""Explicitly approved Google Calendar sync orchestration."""

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
    CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH,
    CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH,
    CalendarRecommendation,
    CalendarRecommendationArtifact,
    CalendarRecommendationMetadata,
)
from backend.app.models.google_calendar_sync import (
    GOOGLE_CALENDAR_SYNC_VERSION,
    CalendarSyncApproval,
    CalendarSyncApprovalRecord,
    CalendarSyncArtifact,
    CalendarSyncAttemptRecord,
    CalendarSyncMetadata,
    CalendarSyncOutputMetadata,
    CalendarSyncResult,
    CalendarSyncSource,
    CalendarSyncTarget,
    CalendarSyncValidationMetadata,
    GoogleCalendarRemoteEvent,
    attempt_relative_path,
    sync_metadata_relative_path,
    sync_relative_path,
)
from backend.app.services.calendar.errors import CalendarRecommendationError
from backend.app.services.calendar.service import CalendarRecommendationService
from backend.app.services.google_calendar.auth import load_google_calendar_credentials
from backend.app.services.google_calendar.errors import (
    GoogleCalendarApprovalRequiredError,
    GoogleCalendarLocalPersistenceAfterRemoteSuccessError,
    GoogleCalendarMetadataWriteError,
    GoogleCalendarPublicationError,
    GoogleCalendarRecommendationNotFoundError,
    GoogleCalendarRecommendationNotReadyError,
    GoogleCalendarRecommendationUnsupportedError,
    GoogleCalendarRemoteConflictError,
    GoogleCalendarRemoteStateError,
    GoogleCalendarSyncError,
    GoogleCalendarSyncStateError,
)
from backend.app.services.google_calendar.gateway import GoogleCalendarGateway
from backend.app.services.google_calendar.google_gateway import GoogleCalendarApiGateway
from backend.app.services.google_calendar.payload import (
    PRIVATE_PROPERTY_MEETING_ID,
    PRIVATE_PROPERTY_RECOMMENDATION_HASH,
    PRIVATE_PROPERTY_RECOMMENDATION_ID,
    PRIVATE_PROPERTY_SYNC_VERSION,
    GoogleCalendarPreparedPayload,
    build_google_calendar_event_payload,
)

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024

Clock = Callable[[], dt.datetime]


@dataclass(frozen=True)
class _Phase9Context:
    meeting_id: str
    meeting_dir: Path
    recommendation: CalendarRecommendation
    recommendations_path: Path
    recommendations_size_bytes: int
    recommendations_sha256: str
    recommendations_metadata_path: Path
    recommendations_metadata_size_bytes: int
    recommendations_metadata_sha256: str

    @property
    def source(self) -> CalendarSyncSource:
        return CalendarSyncSource(
            recommendations_size_bytes=self.recommendations_size_bytes,
            recommendations_sha256=self.recommendations_sha256,
            recommendations_metadata_size_bytes=(
                self.recommendations_metadata_size_bytes
            ),
            recommendations_metadata_sha256=self.recommendations_metadata_sha256,
        )


@dataclass(frozen=True)
class _SyncPaths:
    sync_path: Path
    metadata_path: Path


def sync_approved_calendar_recommendation(
    meeting_id: str,
    approval: CalendarSyncApproval,
    *,
    calendar_id: str | None = None,
    gateway: GoogleCalendarGateway | None = None,
) -> CalendarSyncResult:
    """Sync one explicitly approved Phase 8 recommendation."""

    return GoogleCalendarSyncService().sync_approved_calendar_recommendation(
        meeting_id,
        approval,
        calendar_id=calendar_id,
        gateway=gateway,
    )


class GoogleCalendarSyncService:
    """Create or reuse one approved Google Calendar event for a recommendation."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._clock = clock or _utc_clock

    def sync_approved_calendar_recommendation(
        self,
        meeting_id: str,
        approval: CalendarSyncApproval,
        *,
        calendar_id: str | None = None,
        gateway: GoogleCalendarGateway | None = None,
    ) -> CalendarSyncResult:
        """Create or reuse a Google Calendar event for one approved recommendation."""

        approval = self._validate_approval(approval)
        target_calendar_id = self._resolve_calendar_id(calendar_id)
        context = self._load_phase8_context(meeting_id, approval.recommendation_id)
        self._validate_eligibility(context.recommendation)
        paths = self._sync_paths(context)
        prepared = build_google_calendar_event_payload(
            meeting_id=context.meeting_id,
            calendar_id=target_calendar_id,
            recommendations_sha256=context.recommendations_sha256,
            recommendation=context.recommendation,
        )

        local_reuse = self._reuse_existing_local_if_valid(
            context,
            approval,
            target_calendar_id,
            prepared,
            paths,
            gateway,
        )
        if local_reuse is not None:
            return local_reuse

        remote_gateway = self._resolve_gateway(gateway)
        remote_event, operation = self._sync_remote(
            context,
            target_calendar_id,
            prepared,
            remote_gateway,
        )
        return self._persist_success(
            context,
            approval,
            target_calendar_id,
            prepared,
            remote_event,
            operation,
            paths,
        )

    def _validate_approval(
        self,
        approval: CalendarSyncApproval,
    ) -> CalendarSyncApproval:
        try:
            approval_model = CalendarSyncApproval.model_validate(approval)
        except ValidationError as exc:
            raise GoogleCalendarApprovalRequiredError(
                "Explicit runtime approval is required."
            ) from exc
        if approval_model.confirmed is not True:
            raise GoogleCalendarApprovalRequiredError(
                "Explicit runtime approval is required."
            )
        return approval_model

    def _resolve_calendar_id(self, value: str | None) -> str:
        candidate = self._settings.google_calendar_id if value is None else value
        normalized = candidate.strip()
        if not normalized:
            raise GoogleCalendarSyncStateError("Google Calendar ID must not be empty.")
        if any(ord(character) < 32 for character in normalized):
            raise GoogleCalendarSyncStateError(
                "Google Calendar ID must not contain control characters."
            )
        return normalized

    def _load_phase8_context(
        self,
        meeting_id: str,
        recommendation_id: str,
    ) -> _Phase9Context:
        if MEETING_ID_PATTERN.fullmatch(meeting_id) is None:
            raise GoogleCalendarRecommendationNotFoundError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise GoogleCalendarRecommendationNotFoundError(
                "Meeting package path is invalid."
            )
        recommendations_path = meeting_dir / CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH
        metadata_path = meeting_dir / CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH
        recommendations_exists = recommendations_path.exists()
        metadata_exists = metadata_path.exists()
        if recommendations_exists != metadata_exists:
            raise GoogleCalendarSyncStateError(
                "Meeting package contains partial Phase 8 recommendation state."
            )
        if not recommendations_exists:
            raise GoogleCalendarRecommendationNotFoundError(
                "Phase 8 calendar recommendations are required before sync."
            )

        try:
            phase8 = CalendarRecommendationService(
                self._settings,
            ).generate_calendar_recommendations(meeting_id)
        except CalendarRecommendationError as exc:
            raise GoogleCalendarSyncStateError(
                "Phase 8 calendar recommendations failed validation."
            ) from exc

        recommendation = next(
            (
                item
                for item in phase8.recommendations.recommendations
                if item.recommendation_id == recommendation_id
            ),
            None,
        )
        if recommendation is None:
            raise GoogleCalendarRecommendationNotFoundError(
                "Requested calendar recommendation was not found."
            )
        recommendations_size, recommendations_sha = self._inspect_file(
            recommendations_path,
            GoogleCalendarSyncStateError,
        )
        metadata_size, metadata_sha = self._inspect_file(
            metadata_path,
            GoogleCalendarSyncStateError,
        )
        if recommendations_sha != phase8.metadata.output.recommendations_sha256:
            raise GoogleCalendarSyncStateError(
                "Phase 8 recommendation checksum does not match metadata."
            )
        if not isinstance(phase8.recommendations, CalendarRecommendationArtifact):
            raise GoogleCalendarSyncStateError(
                "Phase 8 recommendation artifact is invalid."
            )
        if not isinstance(phase8.metadata, CalendarRecommendationMetadata):
            raise GoogleCalendarSyncStateError("Phase 8 metadata is invalid.")
        return _Phase9Context(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            recommendation=recommendation,
            recommendations_path=recommendations_path,
            recommendations_size_bytes=recommendations_size,
            recommendations_sha256=recommendations_sha,
            recommendations_metadata_path=metadata_path,
            recommendations_metadata_size_bytes=metadata_size,
            recommendations_metadata_sha256=metadata_sha,
        )

    def _validate_eligibility(self, recommendation: CalendarRecommendation) -> None:
        if recommendation.readiness_status != "ready":
            raise GoogleCalendarRecommendationNotReadyError(
                "Recommendation is not ready for sync."
            )
        if recommendation.review_reasons or recommendation.blocking_reasons:
            raise GoogleCalendarRecommendationNotReadyError(
                "Recommendation has review or blocking reasons."
            )
        recommendation_type = recommendation.recommendation_type
        schedule_shape = recommendation.schedule.shape
        if recommendation_type == "event" and schedule_shape in {"all_day", "timed"}:
            return
        if recommendation_type in {"deadline", "milestone"} and schedule_shape in {
            "all_day",
            "point_in_time",
        }:
            return
        if recommendation_type == "recurring_event" and schedule_shape == "recurring":
            return
        raise GoogleCalendarRecommendationUnsupportedError(
            "Recommendation type or schedule shape is not syncable."
        )

    def _sync_paths(self, context: _Phase9Context) -> _SyncPaths:
        recommendation_id = context.recommendation.recommendation_id
        return _SyncPaths(
            sync_path=context.meeting_dir / sync_relative_path(recommendation_id),
            metadata_path=(
                context.meeting_dir / sync_metadata_relative_path(recommendation_id)
            ),
        )

    def _reuse_existing_local_if_valid(
        self,
        context: _Phase9Context,
        approval: CalendarSyncApproval,
        calendar_id: str,
        prepared: GoogleCalendarPreparedPayload,
        paths: _SyncPaths,
        gateway: GoogleCalendarGateway | None,
    ) -> CalendarSyncResult | None:
        sync_exists = paths.sync_path.exists()
        metadata_exists = paths.metadata_path.exists()
        if sync_exists != metadata_exists:
            raise GoogleCalendarSyncStateError(
                "Meeting package contains partial Google Calendar sync state."
            )
        if not sync_exists:
            return None

        sync = self._load_model(
            paths.sync_path,
            CalendarSyncArtifact,
            "Google Calendar sync artifact is invalid.",
        )
        metadata = self._load_model(
            paths.metadata_path,
            CalendarSyncMetadata,
            "Google Calendar sync metadata is invalid.",
        )
        self._validate_existing_local(
            context,
            approval,
            calendar_id,
            prepared,
            paths,
            sync,
            metadata,
        )
        remote_gateway = self._resolve_gateway(gateway)
        remote_event = remote_gateway.get_event(calendar_id, prepared.event_id)
        if remote_event is None:
            raise GoogleCalendarRemoteStateError(
                "Existing local sync artifact points to a missing remote event."
            )
        self._validate_remote_event(context, prepared, remote_event)
        return CalendarSyncResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            sync_json_path=paths.sync_path.resolve(strict=False),
            sync_metadata_path=paths.metadata_path.resolve(strict=False),
            sync=sync,
            metadata=metadata,
            remote_event=remote_event,
            operation="reused_existing",
            reused_existing=True,
        )

    def _validate_existing_local(
        self,
        context: _Phase9Context,
        approval: CalendarSyncApproval,
        calendar_id: str,
        prepared: GoogleCalendarPreparedPayload,
        paths: _SyncPaths,
        sync: CalendarSyncArtifact,
        metadata: CalendarSyncMetadata,
    ) -> None:
        if sync.meeting_id != context.meeting_id or metadata.meeting_id != context.meeting_id:
            raise GoogleCalendarSyncStateError("Google Calendar sync meeting ID differs.")
        if sync.recommendation_id != context.recommendation.recommendation_id:
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync recommendation ID differs."
            )
        if metadata.recommendation_id != sync.recommendation_id:
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync metadata recommendation ID differs."
            )
        if sync.sync_version != GOOGLE_CALENDAR_SYNC_VERSION:
            raise GoogleCalendarSyncStateError("Google Calendar sync version differs.")
        if metadata.sync_version != GOOGLE_CALENDAR_SYNC_VERSION:
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync metadata version differs."
            )
        if sync.approval.source != approval.source or metadata.approval_source != approval.source:
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync approval source differs."
            )
        if sync.approval.confirmed is not True or approval.confirmed is not True:
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync approval is not explicit."
            )
        if sync.target.calendar_id != calendar_id or metadata.target.calendar_id != calendar_id:
            raise GoogleCalendarSyncStateError("Google Calendar target ID differs.")
        if sync.source.model_dump() != context.source.model_dump():
            raise GoogleCalendarSyncStateError("Google Calendar sync source differs.")
        if metadata.source.model_dump() != context.source.model_dump():
            raise GoogleCalendarSyncStateError(
                "Google Calendar sync metadata source differs."
            )
        if sync.payload_sha256 != prepared.payload_sha256:
            raise GoogleCalendarSyncStateError("Google Calendar payload checksum differs.")
        if sync.remote_event.event_id != prepared.event_id:
            raise GoogleCalendarSyncStateError("Google Calendar event ID differs.")
        sync_size, sync_sha = self._inspect_file(
            paths.sync_path,
            GoogleCalendarSyncStateError,
        )
        expected_output = CalendarSyncOutputMetadata(
            sync_relative_path=sync_relative_path(sync.recommendation_id),
            sync_size_bytes=sync_size,
            sync_sha256=sync_sha,
            google_event_id=prepared.event_id,
            operation=sync.operation,
        )
        if metadata.output.model_dump() != expected_output.model_dump():
            raise GoogleCalendarSyncStateError("Google Calendar sync output differs.")

    def _resolve_gateway(
        self,
        gateway: GoogleCalendarGateway | None,
    ) -> GoogleCalendarGateway:
        if gateway is not None:
            return gateway
        credentials = load_google_calendar_credentials(self._settings)
        return GoogleCalendarApiGateway(credentials)

    def _sync_remote(
        self,
        context: _Phase9Context,
        calendar_id: str,
        prepared: GoogleCalendarPreparedPayload,
        gateway: GoogleCalendarGateway,
    ) -> tuple[GoogleCalendarRemoteEvent, str]:
        try:
            existing = gateway.get_event(calendar_id, prepared.event_id)
        except GoogleCalendarSyncError as exc:
            self._write_attempt_record(
                context,
                prepared,
                calendar_id,
                "failed",
                "get",
                type(exc).__name__,
            )
            raise
        if existing is not None:
            try:
                self._validate_remote_event(context, prepared, existing)
            except GoogleCalendarSyncError as exc:
                self._write_attempt_record(
                    context,
                    prepared,
                    calendar_id,
                    "conflict",
                    "get",
                    type(exc).__name__,
                )
                raise
            return existing, "reused_existing"

        try:
            created = gateway.insert_event(calendar_id, prepared.event_id, prepared.body)
        except GoogleCalendarRemoteConflictError:
            recovered = gateway.get_event(calendar_id, prepared.event_id)
            if recovered is None:
                self._write_attempt_record(
                    context,
                    prepared,
                    calendar_id,
                    "conflict",
                    "insert",
                    "GoogleCalendarRemoteConflictError",
                )
                raise GoogleCalendarRemoteConflictError(
                    "Google Calendar event ID conflict could not be recovered."
                )
            try:
                self._validate_remote_event(context, prepared, recovered)
            except GoogleCalendarSyncError as exc:
                self._write_attempt_record(
                    context,
                    prepared,
                    calendar_id,
                    "conflict",
                    "insert",
                    type(exc).__name__,
                )
                raise
            return recovered, "reused_existing"
        except GoogleCalendarSyncError as exc:
            self._write_attempt_record(
                context,
                prepared,
                calendar_id,
                "failed",
                "insert",
                type(exc).__name__,
            )
            raise
        try:
            self._validate_remote_event(context, prepared, created)
        except GoogleCalendarSyncError as exc:
            self._write_attempt_record(
                context,
                prepared,
                calendar_id,
                "conflict",
                "insert",
                type(exc).__name__,
            )
            raise
        return created, "created"

    def _validate_remote_event(
        self,
        context: _Phase9Context,
        prepared: GoogleCalendarPreparedPayload,
        remote_event: GoogleCalendarRemoteEvent,
    ) -> None:
        if remote_event.event_id != prepared.event_id:
            raise GoogleCalendarRemoteConflictError(
                "Remote Google Calendar event ID differs."
            )
        if remote_event.status == "cancelled":
            raise GoogleCalendarRemoteStateError(
                "Remote Google Calendar event is cancelled."
            )
        expected_private = {
            PRIVATE_PROPERTY_MEETING_ID: context.meeting_id,
            PRIVATE_PROPERTY_RECOMMENDATION_ID: context.recommendation.recommendation_id,
            PRIVATE_PROPERTY_RECOMMENDATION_HASH: context.recommendations_sha256,
            PRIVATE_PROPERTY_SYNC_VERSION: GOOGLE_CALENDAR_SYNC_VERSION,
        }
        for key, expected in expected_private.items():
            if remote_event.private_extended_properties.get(key) != expected:
                raise GoogleCalendarRemoteConflictError(
                    "Remote Google Calendar event provenance differs."
                )

    def _persist_success(
        self,
        context: _Phase9Context,
        approval: CalendarSyncApproval,
        calendar_id: str,
        prepared: GoogleCalendarPreparedPayload,
        remote_event: GoogleCalendarRemoteEvent,
        operation: str,
        paths: _SyncPaths,
    ) -> CalendarSyncResult:
        staging_dir = (
            context.meeting_dir
            / ".staging"
            / f"google_calendar_sync_{context.recommendation.recommendation_id}_{uuid.uuid4().hex}"
        )
        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_sync_path = staging_dir / f"{context.recommendation.recommendation_id}.json"
            staged_metadata_path = (
                staging_dir / f"{context.recommendation.recommendation_id}.metadata.json"
            )
            approval_record = CalendarSyncApprovalRecord(
                recommendation_id=context.recommendation.recommendation_id,
                confirmed=True,
                source=approval.source,
                approved_at_utc=self._utc_now(),
            )
            sync = CalendarSyncArtifact(
                meeting_id=context.meeting_id,
                recommendation_id=context.recommendation.recommendation_id,
                source=context.source,
                approval=approval_record,
                target=CalendarSyncTarget(calendar_id=calendar_id),
                operation=operation,
                payload_sha256=prepared.payload_sha256,
                remote_event=remote_event,
            )
            self._write_json_model(
                sync,
                staged_sync_path,
                GoogleCalendarPublicationError,
                "Google Calendar sync artifact could not be written.",
            )
            sync_size, sync_sha = self._inspect_file(
                staged_sync_path,
                GoogleCalendarPublicationError,
            )
            metadata = CalendarSyncMetadata(
                meeting_id=context.meeting_id,
                created_at_utc=self._utc_now(),
                recommendation_id=context.recommendation.recommendation_id,
                approval_source=approval.source,
                target=CalendarSyncTarget(calendar_id=calendar_id),
                source=context.source,
                output=CalendarSyncOutputMetadata(
                    sync_relative_path=sync_relative_path(
                        context.recommendation.recommendation_id
                    ),
                    sync_size_bytes=sync_size,
                    sync_sha256=sync_sha,
                    google_event_id=prepared.event_id,
                    operation=operation,
                ),
                validation=CalendarSyncValidationMetadata(),
            )
            self._write_json_model(
                metadata,
                staged_metadata_path,
                GoogleCalendarMetadataWriteError,
                "Google Calendar sync metadata could not be written.",
            )
            self._publish_artifacts(
                staged_sync_path,
                paths.sync_path,
                staged_metadata_path,
                paths.metadata_path,
            )
            return CalendarSyncResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                sync_json_path=paths.sync_path.resolve(strict=False),
                sync_metadata_path=paths.metadata_path.resolve(strict=False),
                sync=sync,
                metadata=metadata,
                remote_event=remote_event,
                operation=operation,
                reused_existing=(operation == "reused_existing"),
            )
        except GoogleCalendarSyncError as exc:
            self._write_attempt_record(
                context,
                prepared,
                calendar_id,
                "remote_created_local_persistence_failed",
                "local_persist",
                type(exc).__name__,
            )
            raise GoogleCalendarLocalPersistenceAfterRemoteSuccessError(
                "Local Google Calendar sync persistence failed after remote success.",
                event_id=prepared.event_id,
            ) from exc
        except OSError as exc:
            self._write_attempt_record(
                context,
                prepared,
                calendar_id,
                "remote_created_local_persistence_failed",
                "local_persist",
                type(exc).__name__,
            )
            raise GoogleCalendarLocalPersistenceAfterRemoteSuccessError(
                "Local Google Calendar sync persistence failed after remote success.",
                event_id=prepared.event_id,
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _publish_artifacts(
        self,
        staged_sync_path: Path,
        sync_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published: list[Path] = []
        try:
            sync_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if sync_path.exists() or metadata_path.exists():
                raise GoogleCalendarSyncStateError(
                    "Meeting package contains inconsistent Google Calendar sync state."
                )
            os.replace(staged_sync_path, sync_path)
            published.append(sync_path)
            if metadata_path.exists():
                raise GoogleCalendarSyncStateError(
                    "Meeting package contains inconsistent Google Calendar sync state."
                )
            os.replace(staged_metadata_path, metadata_path)
            published.append(metadata_path)
        except GoogleCalendarSyncError:
            self._remove_published_artifacts(published)
            raise
        except OSError as exc:
            self._remove_published_artifacts(published)
            raise GoogleCalendarPublicationError(
                "Google Calendar sync artifacts could not be published."
            ) from exc

    def _remove_published_artifacts(self, paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.info("Failed to roll back local Google Calendar artifact")

    def _write_attempt_record(
        self,
        context: _Phase9Context,
        prepared: GoogleCalendarPreparedPayload,
        calendar_id: str,
        outcome: str,
        operation: str,
        failure_type: str,
    ) -> None:
        attempt_id = uuid.uuid4().hex
        try:
            record = CalendarSyncAttemptRecord(
                meeting_id=context.meeting_id,
                recommendation_id=context.recommendation.recommendation_id,
                attempt_id=attempt_id,
                created_at_utc=self._utc_now(),
                calendar_id=calendar_id,
                google_event_id=prepared.event_id,
                outcome=outcome,
                operation=operation,
                failure_type=failure_type,
            )
            path = context.meeting_dir / attempt_relative_path(
                context.recommendation.recommendation_id,
                attempt_id,
            )
            self._write_json_model(
                record,
                path,
                GoogleCalendarMetadataWriteError,
                "Google Calendar sync attempt metadata could not be written.",
            )
        except Exception:
            logger.info("Failed to write Google Calendar sync attempt metadata")

    def _write_json_model(
        self,
        model: Any,
        path: Path,
        error_type: type[GoogleCalendarSyncError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[GoogleCalendarSyncError],
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
                logger.info("Failed to remove temporary Google Calendar artifact")
            raise error_type(message) from exc

    def _load_model(
        self,
        path: Path,
        model_type: Any,
        invalid_message: str,
    ) -> Any:
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise GoogleCalendarSyncStateError(invalid_message) from exc

    def _inspect_file(
        self,
        path: Path,
        error_type: type[GoogleCalendarSyncError],
    ) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                raise error_type("Google Calendar sync artifact is missing.")
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise error_type(
                "Google Calendar sync artifact could not be inspected."
            ) from exc
        if size_bytes <= 0:
            raise error_type("Google Calendar sync artifact is empty.")
        return size_bytes, self._hash_file(path, error_type)

    def _hash_file(
        self,
        path: Path,
        error_type: type[GoogleCalendarSyncError],
    ) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise error_type("Artifact could not be hashed.") from exc
        return checksum.hexdigest()

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.info("Failed to roll back staged Google Calendar artifacts")

    def _utc_now(self) -> dt.datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise GoogleCalendarPublicationError(
                "Clock must return a timezone-aware datetime."
            )
        return value.astimezone(dt.timezone.utc)

    def _is_relative_to(self, child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent.resolve(strict=False))
            return True
        except ValueError:
            return False


def _utc_clock() -> dt.datetime:
    return getattr(dt.datetime, "now")(dt.timezone.utc)
