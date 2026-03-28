import pytest
import httpx

from app.core.config import get_settings
from app.schemas.models import ApprovalStatus, CallSession, CallType
from app.services.bland_service import BlandService
from app.services.call_service import CallService
from app.services.seed import seed_data
from app.storage.memory_store import MemoryStore


@pytest.mark.asyncio
async def test_support_call_creation(monkeypatch):
    store = MemoryStore()
    await store.initialize()
    await seed_data(store)

    async def fake_queue_call(self, call):
        return {"status": "success", "call_id": "bland-support-123"}

    monkeypatch.setattr("app.services.bland_service.BlandService.queue_call", fake_queue_call)

    svc = CallService(store)
    user = await store.get_user("user-maya")

    call = await svc.start_support_call(user)

    assert call.call_type.value == "support"
    assert call.vendor_call_id == "bland-support-123"
    assert call.status.value == "queued"


@pytest.mark.asyncio
async def test_answer_question_uses_grounded_data(monkeypatch):
    store = MemoryStore()
    await store.initialize()
    await seed_data(store)

    async def fake_answer(*args, **kwargs):
        return {
            "answer": "Maya has $63 and Penny picked three diversified starter options.",
            "confidence": 0.91,
            "sources": ["How Penny uses the $50 threshold"],
        }

    monkeypatch.setattr("app.services.nim_service.NimService.answer_question", fake_answer)

    svc = CallService(store)
    result = await svc.answer_question("call-001", "How much money do I have?")

    assert "63" in result["answer"]
    call = await store.get_call_session("call-001")
    assert call.last_question == "How much money do I have?"


@pytest.mark.asyncio
async def test_tool_decision_updates_approval_and_recommendation():
    store = MemoryStore()
    await store.initialize()
    await seed_data(store)

    svc = CallService(store)
    approval = await svc.apply_tool_decision("call-002", "approved", "Parent approved by phone")

    assert approval.status == ApprovalStatus.APPROVED
    recommendation = await store.get_recommendation_set("user-maya")
    assert recommendation.status.value == "approved"


@pytest.mark.asyncio
async def test_webhook_updates_call_status():
    store = MemoryStore()
    await store.initialize()
    await seed_data(store)

    svc = CallService(store)
    call = await store.get_call_session("call-001")
    call.vendor_call_id = "vendor-001"
    await store.update_call_session(call)

    updated = await svc.process_webhook(
        {
            "call_id": "vendor-001",
            "status": "completed",
            "summary": "Call completed successfully",
            "transcript": "Grounded transcript",
        }
    )

    assert updated is not None
    assert updated.status.value == "completed"
    assert updated.summary == "Call completed successfully"


@pytest.mark.asyncio
async def test_bland_omits_live_tooling_without_public_callback_url(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bland_api_key", "test-key")
    monkeypatch.setattr(settings, "app_public_url", "http://localhost:8000")

    service = BlandService()
    call = CallSession(
        user_id="user-maya",
        household_id="house-001",
        call_type=CallType.SUPPORT,
        phone_number="+14154819927",
    )

    payload = service.build_support_payload(call)

    assert "tools" not in payload
    assert "dynamic_data" not in payload
    assert "webhook" not in payload
    assert payload["metadata"]["callback_mode"] == "static"


@pytest.mark.asyncio
async def test_bland_uses_curl_fallback_for_tls_errors(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bland_api_key", "test-key")
    monkeypatch.setattr(settings, "app_public_url", "https://demo.example.com")

    async def fake_post_call(self, payload):
        raise httpx.ConnectError(
            "[SSL: TLSV1_ALERT_PROTOCOL_VERSION] tlsv1 alert protocol version",
            request=httpx.Request("POST", "https://api.bland.ai/v1/calls"),
        )

    def fake_curl_fallback(self, payload):
        return {"status": "success", "call_id": "curl-call-123"}

    monkeypatch.setattr(BlandService, "_post_call", fake_post_call)
    monkeypatch.setattr(BlandService, "_queue_call_with_curl", fake_curl_fallback)

    service = BlandService()
    call = CallSession(
        user_id="user-maya",
        household_id="house-001",
        call_type=CallType.SUPPORT,
        phone_number="+14154819927",
    )

    response = await service.queue_call(call)

    assert response["call_id"] == "curl-call-123"
    assert response["transport"] == "curl"
    assert response["payload"]["phone_number"] == "+14154819927"
