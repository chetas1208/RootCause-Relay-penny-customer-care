from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.models import CallSession, CallType


class BlandService:
    def __init__(self):
        self.settings = get_settings()

    def build_support_payload(self, call: CallSession) -> dict[str, Any]:
        live_callbacks = self.settings.app_public_url_is_public
        payload = {
            "phone_number": call.phone_number,
            "voice": self.settings.bland_support_voice_id or "maya",
            "task": self._build_support_task(call, live_callbacks),
            "model": self.settings.bland_model,
            "language": "en",
            "wait_for_greeting": False,
            "record": True,
            "answered_by_enabled": True,
            "noise_cancellation": False,
            "interruption_threshold": 500,
            "block_interruptions": False,
            "max_duration": 12,
            "background_track": "none",
            "metadata": {
                "call_session_id": call.id,
                "call_type": call.call_type.value,
                "household_id": call.household_id,
                "callback_mode": "tooling" if live_callbacks else "static",
            },
        }
        if live_callbacks:
            payload.update(
                {
                    "request_data": {
                        "call_session_id": call.id,
                    },
                    "webhook": f"{self.settings.app_public_url.rstrip('/')}/api/webhooks/bland/call",
                    "webhook_events": ["queue", "call", "tool", "dynamic_data", "webhook"],
                    "dynamic_data": [self._customer_context_dynamic_data(call)],
                    "tools": [self._answer_question_tool(call)],
                }
            )
        return payload

    def build_approval_payload(self, call: CallSession) -> dict[str, Any]:
        live_callbacks = self.settings.app_public_url_is_public
        payload = {
            "phone_number": call.phone_number,
            "voice": self.settings.bland_approval_voice_id or self.settings.bland_support_voice_id or "maya",
            "task": self._build_approval_task(call, live_callbacks),
            "model": self.settings.bland_model,
            "language": "en",
            "wait_for_greeting": False,
            "record": True,
            "answered_by_enabled": True,
            "noise_cancellation": False,
            "interruption_threshold": 500,
            "block_interruptions": False,
            "max_duration": 12,
            "background_track": "none",
            "metadata": {
                "call_session_id": call.id,
                "call_type": call.call_type.value,
                "household_id": call.household_id,
                "approval_request_id": call.approval_request_id,
                "callback_mode": "tooling" if live_callbacks else "static",
            },
        }
        if live_callbacks:
            payload.update(
                {
                    "request_data": {
                        "call_session_id": call.id,
                    },
                    "webhook": f"{self.settings.app_public_url.rstrip('/')}/api/webhooks/bland/call",
                    "webhook_events": ["queue", "call", "tool", "dynamic_data", "webhook"],
                    "dynamic_data": [self._customer_context_dynamic_data(call)],
                    "tools": [self._answer_question_tool(call), self._approval_decision_tool(call)],
                }
            )
        return payload

    async def queue_call(self, call: CallSession) -> dict[str, Any]:
        if call.call_type == CallType.SUPPORT:
            payload = self.build_support_payload(call)
        else:
            payload = self.build_approval_payload(call)

        if not self.settings.bland_api_key:
            return {
                "status": "demo",
                "message": "Bland API key not configured, stored locally only.",
                "call_id": f"demo-{call.id}",
                "payload": payload,
            }

        try:
            response = await self._post_call(payload)
        except httpx.HTTPError as exc:
            if not self._should_retry_with_curl(exc):
                raise
            response = await asyncio.to_thread(self._queue_call_with_curl, payload)
            response = response | {"transport": "curl"}

        return response | {"payload": payload}

    async def _post_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.bland_base_url.rstrip('/')}/calls",
                headers={
                    "authorization": self.settings.bland_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _queue_call_with_curl(self, payload: dict[str, Any]) -> dict[str, Any]:
        status_marker = "__STATUS__:"
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-X",
                "POST",
                f"{self.settings.bland_base_url.rstrip('/')}/calls",
                "-H",
                f"authorization: {self.settings.bland_api_key}",
                "-H",
                "Content-Type: application/json",
                "--data",
                json.dumps(payload),
                "--write-out",
                f"\n{status_marker}%{{http_code}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "curl fallback failed"
            raise RuntimeError(f"Bland curl fallback failed: {detail}")

        body, _, status_code = result.stdout.rpartition(f"\n{status_marker}")
        status = int(status_code or 0)
        if status < 200 or status >= 300:
            detail = body.strip() or result.stderr.strip() or f"HTTP {status}"
            raise RuntimeError(f"Bland request failed with status {status}: {detail}")

        if not body.strip():
            return {}

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Bland curl fallback returned a non-JSON response") from exc

    def _should_retry_with_curl(self, exc: httpx.HTTPError) -> bool:
        message = str(exc).lower()
        return "tlsv1_alert_protocol_version" in message or "tlsv1 alert protocol version" in message

    def _build_support_task(self, call: CallSession, live_callbacks: bool) -> str:
        context = self._call_context(call)
        options_summary = self._options_summary(context)
        tool_line = (
            "If the child asks for deeper account details, use the AnswerQuestion tool before responding."
            if live_callbacks
            else "You do not have external tools in this call, so answer only from the provided account snapshot."
        )
        return (
            "You are Penny, a friendly customer-care guide for a financial literacy app for kids. "
            f"You are speaking with {context['child_name']}. "
            f"Their current Penny balance is {context['balance_amount']}. "
            f"Current approval status: {context['approval_status']}. "
            f"Recommendation summary: {context['recommendation_summary']}. "
            f"Suggested options right now: {options_summary}. "
            "Use a warm, calm, peer-like tone. Keep answers short and practical. "
            f"{tool_line}"
        )

    def _build_approval_task(self, call: CallSession, live_callbacks: bool) -> str:
        context = self._call_context(call)
        options_summary = self._options_summary(context)
        tool_line = (
            "Use AnswerQuestion for grounded follow-up questions, and use ApprovalDecision as soon as the parent clearly approves or declines."
            if live_callbacks
            else "You do not have external tools in this call, so explain the current recommendation and ask the parent to confirm approval later in the app."
        )
        parent_name = context["parent_name"] or "the parent"
        return (
            "You are Penny calling a parent for approval. "
            f"You are speaking with {parent_name}. "
            f"The child is {context['child_name']}, and their current Penny balance is {context['balance_amount']}. "
            f"Recommendation summary: {context['recommendation_summary']}. "
            f"The three current options are: {options_summary}. "
            "Use a warm, concise tone, explain that this is a learning-focused suggestion, and do not sound salesy. "
            f"{tool_line}"
        )

    def _call_context(self, call: CallSession) -> dict[str, Any]:
        context = call.metadata.get("customer_context") if isinstance(call.metadata, dict) else None
        if not isinstance(context, dict):
            return {
                "child_name": "the customer",
                "parent_name": "",
                "balance_amount": "$0.00",
                "approval_status": "not_requested",
                "recommendation_summary": "No recommendation is ready yet.",
                "options": [],
            }
        return {
            "child_name": context.get("child_name") or "the customer",
            "parent_name": context.get("parent_name") or "",
            "balance_amount": context.get("balance_amount") or "$0.00",
            "approval_status": context.get("approval_status") or "not_requested",
            "recommendation_summary": context.get("recommendation_summary") or "No recommendation is ready yet.",
            "options": context.get("options") or [],
        }

    def _options_summary(self, context: dict[str, Any]) -> str:
        options = context.get("options") or []
        if not isinstance(options, list) or not options:
            return "no specific options are on file yet"

        parts = []
        for option in options[:3]:
            if not isinstance(option, dict):
                continue
            name = option.get("name") or option.get("symbol") or "option"
            allocation = option.get("allocation_percent")
            rationale = option.get("rationale") or option.get("risk_level") or "starter option"
            if allocation:
                parts.append(f"{name} at {allocation}% because {rationale}")
            else:
                parts.append(f"{name} because {rationale}")
        return "; ".join(parts) if parts else "no specific options are on file yet"

    def _customer_context_dynamic_data(self, call: CallSession) -> dict[str, Any]:
        return {
            "url": f"{self.settings.app_public_url.rstrip('/')}/api/bland/tools/customer-context",
            "method": "POST",
            "headers": {
                "X-App-Secret": self.settings.app_secret_key,
            },
            "body": {
                "call_session_id": call.id,
            },
            "response_data": [
                {
                    "name": "child_name",
                    "data": "$.child_name",
                    "context": "The child tied to this household is {{child_name}}.",
                },
                {
                    "name": "parent_name",
                    "data": "$.parent_name",
                    "context": "The parent decision-maker is {{parent_name}}.",
                },
                {
                    "name": "balance_amount",
                    "data": "$.balance_amount",
                    "context": "Current Penny balance is {{balance_amount}}.",
                },
                {
                    "name": "recommendation_summary",
                    "data": "$.recommendation_summary",
                    "context": "Current recommendation summary: {{recommendation_summary}}",
                },
                {
                    "name": "approval_status",
                    "data": "$.approval_status",
                    "context": "Approval status right now is {{approval_status}}.",
                },
            ],
        }

    def _answer_question_tool(self, call: CallSession) -> dict[str, Any]:
        return {
            "name": "AnswerQuestion",
            "description": "Use this to answer grounded questions about Penny balances, recommendations, and approval rules.",
            "url": f"{self.settings.app_public_url.rstrip('/')}/api/bland/tools/answer-question",
            "method": "POST",
            "headers": {
                "X-App-Secret": self.settings.app_secret_key,
            },
            "body": {
                "call_session_id": call.id,
                "question": "{{input.question}}",
                "speech": "{{input.speech}}",
            },
            "input_schema": {
                "type": "object",
                "example": {
                    "speech": "Let me check that for you.",
                    "question": "How much money does Maya have and why did Penny choose those three options?",
                },
                "properties": {
                    "speech": "string",
                    "question": "string",
                },
                "required": ["question"],
            },
            "response": {
                "answer": "$.answer",
                "confidence": "$.confidence",
            },
        }

    def _approval_decision_tool(self, call: CallSession) -> dict[str, Any]:
        return {
            "name": "ApprovalDecision",
            "description": "Use this once the parent clearly approves or declines the recommendation.",
            "url": f"{self.settings.app_public_url.rstrip('/')}/api/bland/tools/approval-decision",
            "method": "POST",
            "headers": {
                "X-App-Secret": self.settings.app_secret_key,
            },
            "body": {
                "call_session_id": call.id,
                "decision": "{{input.decision}}",
                "note": "{{input.note}}",
                "speech": "{{input.speech}}",
            },
            "input_schema": {
                "type": "object",
                "example": {
                    "speech": "Thanks, I'll record that approval now.",
                    "decision": "approved",
                    "note": "Parent approved after hearing the three options.",
                },
                "properties": {
                    "speech": "string",
                    "decision": "approved or declined",
                    "note": "string",
                },
                "required": ["decision"],
            },
            "response": {
                "status": "$.status",
            },
        }
