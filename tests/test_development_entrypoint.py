from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from agentic_evo.development_entrypoint import (
    MAX_DEVELOPMENT_ENTRYPOINT_RESULT_BYTES,
    MAX_DEVELOPMENT_ENTRYPOINT_SOURCE_BYTES,
    DevelopmentContext,
    DevelopmentEntrypointError,
    run_development_entrypoint,
)


class DevelopmentEntrypointTests(unittest.TestCase):
    def _context(self, *, calls: list[tuple[str, object]] | None = None) -> DevelopmentContext:
        captured = calls if calls is not None else []

        def recall(before_sequence: int | None, limit: int):
            captured.append(("recall", (before_sequence, limit)))
            return {
                "experiences": [{"payload": {"note": "prior observation"}}],
                "has_more": True,
            }

        def invoke_organ(prompt: str):
            captured.append(("organ", prompt))
            return {
                "final_text": '{"action":"no_change"}',
                "organ_call_ref": "host-call-1",
                "recall_trace": [],
            }

        return DevelopmentContext(
            opportunity={
                "id": "development-1",
                "lineage_facts": {"current_manifest": {"head": "head-1"}},
            },
            body_files={"entrypoint.py": "current Body source"},
            recall_port=recall,
            organ_port=invoke_organ,
        )

    def test_executes_explicit_entrypoint_with_immutable_facts_and_narrow_ports(self) -> None:
        calls: list[tuple[str, object]] = []
        context = self._context(calls=calls)
        source = """
def develop(context):
    for mapping, key in ((context.opportunity, "id"), (context.body_files, "entrypoint.py")):
        try:
            mapping[key] = "changed"
        except TypeError:
            pass
        else:
            raise RuntimeError("context mapping was mutable")
    try:
        context.opportunity["lineage_facts"]["current_manifest"]["head"] = "changed"
    except TypeError:
        pass
    else:
        raise RuntimeError("nested context mapping was mutable")
    recalled = context.recall(before_sequence=5, limit=2)
    try:
        recalled["experiences"][0]["payload"]["note"] = "changed"
    except TypeError:
        pass
    else:
        raise RuntimeError("recall result was mutable")
    response = context.invoke_organ("Inspect the bounded facts.")
    if response["organ_call_ref"] != "host-call-1":
        raise RuntimeError("unexpected organ response")
    return {"action": "no_change"}
"""

        result = run_development_entrypoint(source, context=context)

        self.assertEqual(result, {"action": "no_change"})
        self.assertEqual(calls, [("recall", (5, 2)), ("organ", "Inspect the bounded facts.")])
        self.assertEqual(context.opportunity["id"], "development-1")
        self.assertEqual(
            context.opportunity["lineage_facts"]["current_manifest"]["head"],
            "head-1",
        )
        with self.assertRaises(FrozenInstanceError):
            context.opportunity = {}  # type: ignore[misc]

    def test_rejects_invalid_source_entrypoint_invocation_and_return_boundaries(self) -> None:
        context = self._context()
        cases = (
            (None, "source_invalid"),
            ("def develop(context):\n  return [", "source_compile_failed"),
            ("value = 1", "entrypoint_missing"),
            ("def develop(context):\n  raise RuntimeError('hidden')", "entrypoint_invocation_failed"),
            ("def develop(context):\n  return []", "entrypoint_return_invalid"),
            ("def develop(context):\n  return {'action': {1}}", "entrypoint_return_not_json"),
            ("x" * (MAX_DEVELOPMENT_ENTRYPOINT_SOURCE_BYTES + 1), "source_oversize"),
            (
                "def develop(context):\n  return {'payload': '"
                + ("x" * MAX_DEVELOPMENT_ENTRYPOINT_RESULT_BYTES)
                + "'}",
                "entrypoint_return_oversize",
            ),
        )

        for source, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(DevelopmentEntrypointError) as caught:
                    run_development_entrypoint(source, context=context)  # type: ignore[arg-type]
                self.assertEqual(caught.exception.code, code)

    def test_does_not_treat_python_as_a_sandbox(self) -> None:
        context = self._context()

        with self.assertRaises(SystemExit):
            run_development_entrypoint(
                "def develop(context):\n  raise SystemExit(7)",
                context=context,
            )


if __name__ == "__main__":
    unittest.main()
