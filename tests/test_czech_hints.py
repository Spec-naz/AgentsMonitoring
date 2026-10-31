import re
import unittest

from agentsmon import dashboard

PAGE = dashboard.PAGE
SCRIPT = re.findall(r"<script>(.*?)</script>", PAGE, re.S)[-1]


class CzechHintTests(unittest.TestCase):
    def test_fixed_dictionary_with_key_texts(self):
        self.assertIn("const HINTS={", SCRIPT)
        for key, fragment in {
            "no fresh bound hook": "Neznamená to, že je agent vypnutý",
            "codex hook": "Popisuje konverzaci, ne postup úkolu",
            "–": "Hodnota chybí",
            "connection details": "Rozbalí technické údaje",
            "process running": "Neříká, jestli agent právě na něčem pracuje",
        }.items():
            self.assertRegex(SCRIPT, re.escape(f'"{key}":"') + r"[^\"]*" + re.escape(fragment))

    def test_every_static_anchor_has_a_text(self):
        keys = set(re.findall(r'^\s*"([^"]+)":"', SCRIPT[SCRIPT.index("const HINTS={"):], re.M))
        for anchor in re.findall(r'data-hint="([a-z][^"$]*)"', PAGE):
            self.assertIn(anchor, keys)

    def test_accessible_popover_and_closing(self):
        self.assertIn('<div id="hint-pop" class="hint-pop" role="tooltip" hidden>', PAGE)
        self.assertIn('setAttribute("aria-label","Nápověda")', SCRIPT)
        self.assertIn('e.key==="Escape"', SCRIPT)
        self.assertIn('!e.target.closest("#hint-pop")) hideHint()', SCRIPT)
        self.assertIn('aria-describedby', SCRIPT)

    def test_no_external_or_ai_calls(self):
        fetched = re.findall(r'fetch\("([^"]+)"', SCRIPT)
        self.assertTrue(fetched)
        self.assertTrue(all(u.startswith("/api/") for u in fetched), fetched)
        hints = SCRIPT[SCRIPT.index("const HINTS={"):SCRIPT.index("// ---- Quick links")]
        self.assertNotIn("fetch(", hints)
        self.assertNotRegex(hints, r"https?://")

    def test_hint_button_is_separate_from_existing_click_targets(self):
        self.assertIn('(el.closest("details")||el).after(btn)', SCRIPT)
        self.assertIn('e.preventDefault(); e.stopPropagation();', SCRIPT)
        self.assertIn('class="agent-name cursor-pointer hover:text-sky-600" data-copy=', SCRIPT)


if __name__ == "__main__":
    unittest.main()
