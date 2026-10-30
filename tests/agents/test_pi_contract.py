import threading


def test_contract_defaults():
    from src.agents.pi_contract import (
        PiApprovalDecision,
        PiAvailability,
        PiRunRequest,
        PiRunResult,
        PiRuntimeConfig,
    )

    assert PiAvailability(False, "missing") == PiAvailability(False, "missing")
    assert PiRuntimeConfig().backend == "auto"
    assert PiRuntimeConfig().agent_names == ["pi"]
    assert PiRuntimeConfig().tool_policy == "read_only"
    assert PiRuntimeConfig().trusted_roots == ["."]
    assert PiRunRequest("j", "s", "m", "r", "p").history == []
    assert PiRunRequest("j", "s", "m", "r", "p").attachments == []
    assert PiRunRequest("j", "s", "m", "r", "p").metadata == {}
    assert PiApprovalDecision("a", False).payload == {}
    assert PiRunResult().metadata == {}


def test_startup_timeout_default_and_bridge_floor():
    from src.agents.pi_contract import PI_BRIDGE_MIN_STARTUP_TIMEOUT_MS, PiRuntimeConfig

    assert PI_BRIDGE_MIN_STARTUP_TIMEOUT_MS == 15000
    assert PiRuntimeConfig().startup_timeout_ms == 15000
    assert PiRuntimeConfig.from_dict({}).startup_timeout_ms == 15000
    assert PiRuntimeConfig.from_dict({"startup_timeout_ms": 6000}).startup_timeout_ms == 6000
    assert PiRuntimeConfig.from_dict({"startup_timeout_ms": 6000}).bridge_startup_timeout_ms == 15000
    assert PiRuntimeConfig.from_dict({"startup_timeout_ms": 30000}).bridge_startup_timeout_ms == 30000
    assert PiRuntimeConfig().bridge_startup_timeout_ms == 15000


def test_runtime_config_normalizes_tool_policy_and_trusted_roots():
    from src.agents.pi_contract import PiRuntimeConfig

    explicit = PiRuntimeConfig.from_dict({
        "tool_policy": " approve_mutations ",
        "trusted_roots": [".", " C:/shared ", "", 42],
        "approval_timeout_ms": 4321,
    })
    assert explicit.tool_policy == "approve_mutations"
    assert explicit.trusted_roots == [".", "C:/shared", "42"]
    assert explicit.approval_timeout_ms == 4321

    denied = PiRuntimeConfig.from_dict({"tool_policy": "full", "trusted_roots": []})
    assert denied.tool_policy == "none"
    assert denied.trusted_roots == []

    defaults = PiRuntimeConfig.from_dict({})
    assert defaults.backend == "auto"
    assert defaults.tool_policy == "read_only"
    assert defaults.trusted_roots == ["."]


def test_run_result_metadata_is_not_shared():
    from src.agents.pi_contract import PiRunResult

    first = PiRunResult()
    second = PiRunResult()
    first.metadata["x"] = 1
    assert second.metadata == {}


def test_runtime_protocol_accepts_expected_signature_shape():
    from src.agents.pi_contract import PiRunRequest, PiRunResult, PiRuntime

    class FakeRuntime:
        def run(self, request: PiRunRequest, cancel_event: threading.Event) -> PiRunResult:
            return PiRunResult(text=request.prompt_text)

        def cancel(self, reason: str = "") -> None:
            return None

        def resolve_approval(self, job_id, approval_id, approved, reason="", payload=None):
            return True

        def close(self, timeout_ms: int = 3000) -> None:
            return None

    assert isinstance(FakeRuntime(), PiRuntime)
