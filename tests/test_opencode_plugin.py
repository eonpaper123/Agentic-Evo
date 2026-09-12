from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agentic_evo.adapters.opencode import (
    install_opencode_plugin,
    render_opencode_plugin,
)


class OpencodePluginTests(unittest.TestCase):
    def test_rendered_plugin_uses_official_context_and_tool_boundaries(self) -> None:
        launcher = Path("C:/Agentic Evo/agentic-evo.cmd")
        home = Path("C:/Agentic Evo/home")

        rendered = render_opencode_plugin(launcher=launcher, home=home)

        self.assertIn(json.dumps(str(launcher.resolve(strict=False))), rendered)
        self.assertIn(json.dumps(str(home.resolve(strict=False))), rendered)
        self.assertIn('\"experimental.chat.system.transform\"', rendered)
        self.assertIn("output.system.push(context)", rendered)
        self.assertIn('\"tool.execute.after\"', rendered)
        self.assertIn("agentic_evo_recall_experiences", rendered)
        self.assertIn("agentic_evo_submit_successor", rendered)
        self.assertIn("message.part.updated", rendered)
        self.assertNotIn("hookSpecificOutput", rendered)
        self.assertNotIn("AGENTIC_EVO_HOME", rendered)

    def test_install_writes_the_explicit_open_code_plugin_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / ".opencode" / "plugins" / "agentic-evo.ts"
            installed = install_opencode_plugin(
                destination=destination,
                launcher=Path("C:/agentic-evo/agentic-evo.cmd"),
                home=Path("C:/agentic-evo/home"),
            )

            self.assertEqual(installed, destination)
            self.assertTrue(destination.is_file())
            self.assertIn("AgenticEvo", destination.read_text(encoding="utf-8"))

    def test_rendered_javascript_has_valid_module_syntax(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable for the focused plugin syntax check")
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "agentic-evo.mjs"
            plugin.write_text(
                render_opencode_plugin(
                    launcher=Path("C:/agentic-evo/agentic-evo.cmd"),
                    home=Path("C:/agentic-evo/home"),
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [node, "--check", str(plugin)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rendered_plugin_factory_wakes_and_injects_context(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable for the focused plugin smoke test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin = root / "agentic-evo.mjs"
            plugin.write_text(
                render_opencode_plugin(
                    launcher=Path("C:/agentic-evo/agentic-evo.cmd"),
                    home=Path("C:/agentic-evo/home"),
                ),
                encoding="utf-8",
            )
            package = root / "node_modules" / "@opencode-ai" / "plugin"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                '{"name":"@opencode-ai/plugin","type":"module","exports":"./index.js"}',
                encoding="utf-8",
            )
            (package / "index.js").write_text(
                """
function chain() {
  return {
    int: () => chain(), min: () => chain(), max: () => chain(),
    optional: () => chain(), positive: () => chain(),
  }
}
export const tool = Object.assign((definition) => definition, {
  schema: {
    number: () => chain(), string: () => chain(), record: () => chain(),
  },
})
""",
                encoding="utf-8",
            )
            smoke = root / "smoke.mjs"
            smoke.write_text(
                """
const encoder = new TextEncoder()
const decoder = new TextDecoder()
const calls = []
function stream(text) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}
globalThis.Bun = {
  spawn(arguments_, options) {
    calls.push({
      arguments_,
      payload: options.stdin === "ignore" ? undefined : JSON.parse(decoder.decode(options.stdin)),
    })
    return {
      exited: Promise.resolve(0),
      stdout: stream('{"context":"smoke context"}'),
      stderr: stream(""),
    }
  },
}
const { AgenticEvo } = await import("./agentic-evo.mjs")
const plugin = await AgenticEvo({ directory: "C:/workspace/project" })
await plugin.event({
  event: { type: "session.created", properties: { info: { id: "session-smoke" } } },
})
const output = { system: [] }
await plugin["experimental.chat.system.transform"]({ sessionID: "session-smoke" }, output)
if (output.system.length !== 1 || output.system[0] !== "smoke context") {
  throw new Error("factory did not inject the returned context")
}
const otherPlugin = await AgenticEvo({ directory: "C:/workspace/other" })
await otherPlugin.event({
  event: { type: "session.created", properties: { info: { id: "session-smoke" } } },
})
if (
  calls.length !== 2 ||
  calls[0].payload.directory !== "C:/workspace/project" ||
  calls[1].payload.directory !== "C:/workspace/other"
) {
  throw new Error("factory did not pass its directory to the hook boundary")
}
""",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [node, str(smoke)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
