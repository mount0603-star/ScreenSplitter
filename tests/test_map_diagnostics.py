"""Execute the real host DOM probe with a read-only dashboard fixture (Node, no npm)."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class MapDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node is needed to execute the browser JavaScript fixture")
        source = Path(__file__).resolve().parents[1] / "webview_player.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        cls.script = next(
            node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "probe_script"
                for target in node.targets
            )
        )

    def probe(self, **fixture):
        harness = r"""
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const f = input.fixture, reads = [];
const RealDate = Date;
global.Date = class extends RealDate {
    constructor() { super(f.now || '2026-09-29T12:00:00'); }
};
global.location = {
    origin: `http://${f.hostname || 'itom.lkeng.com.tw'}:${f.port || '8080'}`,
    hostname: f.hostname || 'itom.lkeng.com.tw',
    pathname: f.pathname || '/overview',
    href: 'http://itom.lkeng.com.tw:8080/overview'
};
global.localStorage = {
    getItem(key) {
        reads.push(key);
        if (f.storageDenied) throw new Error('Access denied');
        const values = {
            'wr.carto.tile-usage.v1': JSON.stringify(f.usage || {}),
            'wr.carto.settings.v1': f.invalidJson ? '{' : JSON.stringify(f.settings || {})
        };
        if (!(key in values)) throw new Error('Unexpected storage read');
        return values[key];
    },
    setItem() { throw new Error('Probe must never mutate storage'); },
    removeItem() { throw new Error('Probe must never mutate storage'); }
};
const map = {
    getBoundingClientRect: () => ({width: 742, height: 556}),
    querySelectorAll(selector) {
        if (selector === 'img.leaflet-tile') return [
            {complete: true, naturalWidth: 256},
            {complete: true, naturalWidth: 0},
            {complete: false, naturalWidth: 0}
        ];
        if (selector === 'div.leaflet-tile') return [{}, {}];
        throw new Error('Unexpected map selector');
    }
};
global.document = {
    documentElement: {outerHTML: '<html>' + 'x'.repeat(100) + '</html>'},
    body: {innerText: 'dashboard', getBoundingClientRect: () => ({width: 1920, height: 1080})},
    readyState: 'complete', title: 'dashboard', hidden: false,
    querySelectorAll: () => [],
    querySelector: () => f.noMap ? null : map
};
const result = eval(input.script);
process.stdout.write(JSON.stringify({result, reads}));
"""
        run = subprocess.run(
            [self.node, "-e", harness],
            input=json.dumps({"script": self.script, "fixture": fixture}),
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
        )
        return json.loads(run.stdout)

    def test_tiles_and_current_month_budget_are_read_only_evidence(self):
        outcome = self.probe(usage={"month": "2026-09", "count": 100000, "secret": "not-returned"})
        report = outcome["result"]["mapDiagnostics"]
        self.assertEqual((report["width"], report["height"]), (742, 556))
        self.assertEqual((report["images"], report["loaded"], report["failed"], report["placeholders"]), (3, 1, 1, 2))
        self.assertEqual(report["carto"]["reason"], "budget")
        self.assertFalse(report["carto"]["currentAllowed"])
        self.assertEqual(outcome["result"]["readyState"], "complete")
        self.assertNotIn("error", outcome["result"])
        self.assertNotIn("not-returned", json.dumps(outcome))
        self.assertEqual(outcome["reads"], ["wr.carto.tile-usage.v1", "wr.carto.settings.v1"])

    def test_scope_never_reads_other_pages_or_hosts(self):
        for fixture in ({"hostname": "other.example"}, {"pathname": "/site-status"},
                        {"hostname": "itom.lkeng.com.tw.evil.example"}, {"port": "8081"}):
            with self.subTest(fixture=fixture):
                outcome = self.probe(**fixture)
                self.assertNotIn("mapDiagnostics", outcome["result"])
                self.assertEqual(outcome["reads"], [])

    def test_invalid_current_month_count_is_unknown_instead_of_allowed(self):
        for value in ("100000", None, []):
            with self.subTest(value=value):
                carto = self.probe(usage={"month": "2026-09", "count": value})["result"]["mapDiagnostics"]["carto"]
                self.assertIsNone(carto["currentAllowed"])
                self.assertIsNone(carto["count"])
                self.assertEqual(carto["reason"], "usage_unavailable")

    def test_schedule_boundaries_overnight_and_disabled(self):
        cases = [
            ({"now": "2026-09-29T07:59:00"}, "schedule"),
            ({"now": "2026-09-29T08:00:00"}, "allowed"),
            ({"now": "2026-09-29T21:00:00"}, "schedule"),
            ({"settings": {"enabled": False}}, "disabled"),
            ({"settings": {"scheduleEnabled": False}, "now": "2026-09-29T23:00:00"}, "allowed"),
            ({"settings": {"activeStart": "21:00", "activeEnd": "08:00"}, "now": "2026-09-29T23:00:00"}, "allowed"),
            ({"settings": {"activeStart": "21:00", "activeEnd": "08:00"}}, "schedule"),
            ({"settings": {"activeStart": "08:00", "activeEnd": "08:00"}, "now": "2026-09-29T23:00:00"}, "allowed"),
        ]
        for fixture, reason in cases:
            with self.subTest(fixture=fixture):
                carto = self.probe(**fixture)["result"]["mapDiagnostics"]["carto"]
                self.assertEqual(carto["reason"], reason)
                self.assertEqual(carto["currentAllowed"], reason == "allowed")

    def test_prior_month_and_empty_map_do_not_change_health(self):
        outcome = self.probe(noMap=True, usage={"month": "2026-08", "count": 100000})
        report = outcome["result"]["mapDiagnostics"]
        self.assertFalse(report["present"])
        self.assertEqual(report["images"], 0)
        self.assertEqual(report["carto"]["month"], "2026-09")
        self.assertEqual(report["carto"]["count"], 0)
        self.assertEqual(report["carto"]["reason"], "allowed")
        self.assertNotIn("error", outcome["result"])

    def test_unavailable_storage_does_not_fail_page_probe(self):
        for fixture in ({"storageDenied": True}, {"invalidJson": True}):
            with self.subTest(fixture=fixture):
                result = self.probe(**fixture)["result"]
                self.assertEqual(result["mapDiagnostics"]["carto"], {
                    "currentAllowed": None, "reason": "storage_unavailable"
                })
                self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
