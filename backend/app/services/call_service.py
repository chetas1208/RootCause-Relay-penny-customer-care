from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.schemas.models import (
    ApprovalRequest,
    ApprovalStatus,
    CallEvent,
    CallSession,
    CallStatus,
    CallType,
    RecommendationStatus,
    TraceSpan,
    User,
    UserRole,
    now,
)
from app.storage.base import StorageAdapter

from .bland_service import BlandService
from .nim_service import NimService


class CallService:
    def __init__(self, store: StorageAdapter):
        self.store = store
        self.bland = BlandService()
        self.nim = NimService()

    async def start_support_call(self, user: User, phone_number: str | None = None) -> CallSession:
        profile = await self.store.get_profile(user.id)
        target_phone = phone_number or (profile.phone_number if profile else None) or user.phone_number
        if not target_phone:
            raise HTTPException(status_code=400, detail="A phone number is required before placing a support call")

        call = CallSession(
            user_id=user.id,
            household_id=user.household_id or "",
            call_type=CallType.SUPPORT,
            phone_number=target_phone,
        )
        call.metadata = await self._build_call_context(subject_user_id=user.id)
        await self.store.save_call_session(call)
        await self.store.save_call_event(CallEvent(call_session_id=call.id, event_type="support_call_requested"))
        await self._trace(call.id, "support_call_requested", {"phone_number": target_phone})

        try:
            provider_response = await self.bland.queue_call(call)
            call.vendor_call_id = provider_response.get("call_id")
            call.metadata = {
                **call.metadata,
                "provider": "bland",
                "provider_response": provider_response,
            }
            call.updated_at = now()
            await self.store.update_call_session(call)
            await self.store.save_call_event(
                CallEvent(
                    call_session_id=call.id,
                    event_type="provider_queued",
                    payload=provider_response,
                )
            )
            await self._trace(call.id, "support_call_queued", {"provider_call_id": call.vendor_call_id})
        except Exception as exc:
            call.status = CallStatus.FAILED
            call.summary = str(exc)
            call.updated_at = now()
            await self.store.update_call_session(call)
            await self.store.save_call_event(
                CallEvent(call_session_id=call.id, event_type="provider_failed", payload={"error": str(exc)})
            )
            await self._trace(call.id, "support_call_failed", {"error": str(exc)}, status="error")
            raise HTTPException(status_code=502, detail=f"Failed to queue support call: {exc}") from exc

        return call

    async def start_approval_call(
        self,
        user: User,
        approval_request_id: str | None = None,
        phone_number: str | None = None,
    ) -> CallSession:
        approval = None
        if approval_request_id:
            approval = await self.store.get_approval_request(approval_request_id)
        elif user.role == UserRole.CHILD:
            recommendation = await self.store.get_recommendation_set(user.id)
            if recommendation:
                approval = await self.store.get_approval_for_recommendation(recommendation.id)

        if not approval:
            raise HTTPException(status_code=404, detail="Approval request not found")

        parent_user = await self.store.get_user(approval.parent_user_id)
        if not parent_user:
            raise HTTPException(status_code=404, detail="Parent user not found")

        parent_profile = await self.store.get_profile(parent_user.id)
        target_phone = phone_number or (parent_profile.phone_number if parent_profile else None) or parent_user.phone_number
        if not target_phone:
            raise HTTPException(status_code=400, detail="Parent phone number is required before placing an approval call")

        call = CallSession(
            user_id=parent_user.id,
            household_id=approval.household_id,
            call_type=CallType.APPROVAL,
            phone_number=target_phone,
            recommendation_set_id=approval.recommendation_set_id,
            approval_request_id=approval.id,
        )
        call.metadata = await self._build_call_context(subject_user_id=approval.child_user_id, approval=approval)
        await self.store.save_call_session(call)
        await self.store.save_call_event(
            CallEvent(
                call_session_id=call.id,
                event_type="approval_call_requested",
                payload={"approval_request_id": approval.id},
            )
        )
        await self._trace(call.id, "approval_call_requested", {"approval_request_id": approval.id})

        try:
            provider_response = await self.bland.queue_call(call)
            call.vendor_call_id = provider_response.get("call_id")
            call.metadata = {
                **call.metadata,
                "provider": "bland",
                "provider_response": provider_response,
            }
            call.updated_at = now()
            await self.store.update_call_session(call)
            await self.store.save_call_event(
                CallEvent(
                    call_session_id=call.id,
                    event_type="provider_queued",
                    payload=provider_response,
                )
            )
            await self._trace(call.id, "approval_call_queued", {"provider_call_id": call.vendor_call_id})
        except Exception as exc:
            call.status = CallStatus.FAILED
            call.summary = str(exc)
            call.updated_at = now()
            await self.store.update_call_session(call)
            await self.store.save_call_event(
                CallEvent(call_session_id=call.id, event_type="provider_failed", payload={"error": str(exc)})
            )
            await self._trace(call.id, "approval_call_failed", {"error": str(exc)}, status="error")
            raise HTTPException(status_code=502, detail=f"Failed to queue approval call: {exc}") from exc

        return call

    async def get_call_detail(self, user: User, call_id: str) -> tuple[CallSession, list[CallEvent], list[TraceSpan]]:
        call = await self.store.get_call_session(call_id)
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        self._ensure_access(user, call)
        events = await self.store.list_call_events(call_id)
        traces = await self.store.get_traces(call_id)
        return call, events, traces

    async def list_calls_for_user(self, user: User) -> list[CallSession]:
        if user.role == UserRole.ADMIN:
            return await self.store.list_call_sessions(limit=50)
        if user.role == UserRole.PARENT:
            return await self.store.list_call_sessions(household_id=user.household_id, limit=50)
        return await self.store.list_call_sessions(user_id=user.id, limit=50)

    async def get_customer_context(self, call_session_id: str) -> dict[str, Any]:
        call = await self.store.get_call_session(call_session_id)
        if not call:
            raise HTTPException(status_code=404, detail="Call session not found")

        user = await self.store.get_user(call.user_id)
        profile = await self.store.get_profile(call.user_id)
        recommendation = None
        approval = None
        if call.recommendation_set_id:
            recommendation = await self._get_recommendation_by_id(call.recommendation_set_id, call.user_id)
        else:
            recommendation = await self.store.get_recommendation_set(call.user_id)
        if recommendation:
            approval = await self.store.get_approval_for_recommendation(recommendation.id)
        options = await self.store.list_recommendation_options(recommendation.id) if recommendation else []

        parent_name = ""
        if approval:
            parent_user = await self.store.get_user(approval.parent_user_id)
            parent_name = parent_user.name if parent_user else ""

        return {
            "child_name": user.name if user else "",
            "parent_name": parent_name,
            "balance_amount": f"${(profile.balance_cents / 100):.2f}" if profile else "$0.00",
            "recommendation_summary": recommendation.summary if recommendation else "No recommendation is ready yet.",
            "approval_status": approval.status.value if approval else "not_requested",
            "options": [option.model_dump() for option in options],
        }

    async def answer_question(self, call_session_id: str, question: str) -> dict[str, Any]:
        call = await self.store.get_call_session(call_session_id)
        if not call:
            raise HTTPException(status_code=404, detail="Call session not found")

        subject_user_id = call.user_id
        if call.call_type == CallType.APPROVAL and call.approval_request_id:
            approval = await self.store.get_approval_request(call.approval_request_id)
            if approval:
                subject_user_id = approval.child_user_id

        profile = await self.store.get_profile(subject_user_id)
        recommendation = await self.store.get_recommendation_set(subject_user_id)
        options = await self.store.list_recommendation_options(recommendation.id) if recommendation else []
        articles = await self.store.search_knowledge_articles(question, limit=3)

        result = await self.nim.answer_question(question, profile, recommendation, options, articles)

        call.last_question = question
        call.answer_source = "ghost+nim" if result.get("confidence", 0) >= 0.7 else "ghost+fallback"
        call.updated_at = now()
        await self.store.update_call_session(call)
        await self.store.save_call_event(
            CallEvent(
                call_session_id=call.id,
                event_type="question_answered",
                payload=result | {"question": question},
            )
        )
        await self._trace(call.id, "answer_question", {"question": question, "confidence": result.get("confidence", 0.0)})
        return result

    async def apply_manual_decision(
        self,
        approval_id: str,
        status: ApprovalStatus,
        note: str,
        source: str,
    ) -> ApprovalRequest:
        approval = await self.store.get_approval_request(approval_id)
        if not approval:
            raise HTTPException(status_code=404, detail="Approval request not found")

        approval.status = status
        approval.decision_source = source
        approval.resolution_note = note
        approval.resolved_at = now()
        await self.store.update_approval_request(approval)
        await self._update_recommendation_status(approval)
        return approval

    async def apply_tool_decision(self, call_session_id: str, decision: str, note: str = "") -> ApprovalRequest:
        call = await self.store.get_call_session(call_session_id)
        if not call or not call.approval_request_id:
            raise HTTPException(status_code=404, detail="Approval call session not found")

        normalized = self._normalize_decision(decision)
        approval = await self.apply_manual_decision(
            call.approval_request_id,
            normalized,
            note,
            source="bland_tool",
        )
        call.status = CallStatus.APPROVED if normalized == ApprovalStatus.APPROVED else CallStatus.DECLINED
        call.summary = f"Parent {normalized.value} the recommendation."
        call.updated_at = now()
        await self.store.update_call_session(call)
        await self.store.save_call_event(
            CallEvent(
                call_session_id=call.id,
                event_type="approval_recorded",
                payload={"status": normalized.value, "note": note},
            )
        )
        await self._trace(call.id, "approval_decision_recorded", {"status": normalized.value})
        return approval

    async def process_webhook(self, payload: dict[str, Any]) -> CallSession | None:
        vendor_call_id = payload.get("call_id")
        metadata = payload.get("metadata") or {}
        call_session_id = metadata.get("call_session_id")
        call = None
        if vendor_call_id:
            call = await self.store.get_call_session_by_vendor_id(vendor_call_id)
        if not call and call_session_id:
            call = await self.store.get_call_session(call_session_id)
        if not call:
            return None

        status_value = payload.get("status") or payload.get("event")
        if status_value:
            call.status = self._map_call_status(str(status_value))
        transcript = payload.get("concatenated_transcript") or payload.get("transcript")
        if transcript:
            call.transcript = transcript
        if payload.get("summary"):
            call.summary = payload["summary"]
        if call.status in (CallStatus.COMPLETED, CallStatus.APPROVED, CallStatus.DECLINED, CallStatus.FAILED):
            call.ended_at = now()
        elif call.status == CallStatus.ACTIVE and not call.started_at:
            call.started_at = now()
        call.updated_at = now()
        await self.store.update_call_session(call)
        await self.store.save_call_event(
            CallEvent(
                call_session_id=call.id,
                event_type=f"webhook:{status_value or 'update'}",
                payload=payload,
            )
        )
        await self._trace(call.id, "bland_webhook_processed", {"status": status_value or "update"})
        return call

    async def _update_recommendation_status(self, approval: ApprovalRequest) -> None:
        recommendation = await self._get_recommendation_by_id(approval.recommendation_set_id, approval.child_user_id)
        if not recommendation:
            return
        recommendation.approval_request_id = approval.id
        recommendation.status = (
            RecommendationStatus.APPROVED
            if approval.status == ApprovalStatus.APPROVED
            else RecommendationStatus.DECLINED
        )
        recommendation.updated_at = now()
        await self.store.update_recommendation_set(recommendation)

    async def _get_recommendation_by_id(self, recommendation_set_id: str, child_user_id: str) -> Any:
        recommendation = await self.store.get_recommendation_set(child_user_id)
        if recommendation and recommendation.id == recommendation_set_id:
            return recommendation
        return recommendation

    def _normalize_decision(self, decision: str) -> ApprovalStatus:
        text = decision.strip().lower()
        if text in {"approve", "approved", "yes", "y"}:
            return ApprovalStatus.APPROVED
        if text in {"decline", "declined", "no", "n"}:
            return ApprovalStatus.DECLINED
        raise HTTPException(status_code=400, detail="Decision must be approved or declined")

    def _map_call_status(self, status_value: str) -> CallStatus:
        text = status_value.strip().lower()
        if text in {"queued", "queue"}:
            return CallStatus.QUEUED
        if text in {"active", "started", "in_progress", "call_started"}:
            return CallStatus.ACTIVE
        if text in {"approved"}:
            return CallStatus.APPROVED
        if text in {"declined"}:
            return CallStatus.DECLINED
        if text in {"failed", "error"}:
            return CallStatus.FAILED
        if text in {"completed", "ended", "call_completed"}:
            return CallStatus.COMPLETED
        if text in {"no_answer", "busy", "voicemail"}:
            return CallStatus.NO_ANSWER
        return CallStatus.QUEUED

    def _ensure_access(self, user: User, call: CallSession) -> None:
        if user.role == UserRole.ADMIN:
            return
        if user.role == UserRole.PARENT and user.household_id == call.household_id:
            return
        if user.id == call.user_id:
            return
        raise HTTPException(status_code=403, detail="You do not have access to this call")

    async def _build_call_context(
        self,
        subject_user_id: str,
        approval: ApprovalRequest | None = None,
    ) -> dict[str, Any]:
        child_user = await self.store.get_user(subject_user_id)
        profile = await self.store.get_profile(subject_user_id)
        recommendation = await self.store.get_recommendation_set(subject_user_id)
        if not approval and recommendation:
            approval = await self.store.get_approval_for_recommendation(recommendation.id)
        options = await self.store.list_recommendation_options(recommendation.id) if recommendation else []

        parent_name = ""
        if approval:
            parent_user = await self.store.get_user(approval.parent_user_id)
            parent_name = parent_user.name if parent_user else ""

        return {
            "customer_context": {
                "child_name": child_user.name if child_user else "the customer",
                "parent_name": parent_name,
                "balance_amount": f"${(profile.balance_cents / 100):.2f}" if profile else "$0.00",
                "coin_balance": profile.coin_balance if profile else 0,
                "favorite_topics": list(profile.favorite_topics) if profile else [],
                "recommendation_summary": recommendation.summary if recommendation else "No recommendation is ready yet.",
                "approval_status": approval.status.value if approval else "not_requested",
                "options": [
                    {
                        "name": option.name,
                        "symbol": option.symbol,
                        "allocation_percent": option.allocation_percent,
                        "risk_level": option.risk_level,
                        "rationale": option.rationale,
                    }
                    for option in options
                ],
            }
        }

    async def _trace(self, call_session_id: str, operation: str, metadata: dict[str, Any], status: str = "ok") -> None:
        started_at = now()
        await self.store.save_trace(
            TraceSpan(
                call_session_id=call_session_id,
                operation=operation,
                status=status,
                start_time=started_at,
                end_time=now(),
                duration_ms=1,
                metadata=metadata,
            )
        )
