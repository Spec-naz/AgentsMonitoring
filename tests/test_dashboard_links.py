import unittest

from agentsmon import dashboard


class LinksTests(unittest.TestCase):
    def test_valid_https_link_is_passed_with_host(self):
        out = dashboard._links({"links": [{"title": "Brain", "url": "https://mozek.example.cz/", "note": "Team wiki"}]})
        self.assertEqual(out, [{"title": "Brain", "url": "https://mozek.example.cz/",
                                "note": "Team wiki", "host": "mozek.example.cz"}])

    def test_unsafe_or_malformed_links_are_dropped(self):
        bad = [
            {"title": "js", "url": "javascript:alert(1)"},
            {"title": "data", "url": "data:text/html,<b>x</b>"},
            {"title": "plain", "url": "http://example.com"},
            {"title": "relative", "url": "/api/state"},
            {"title": "nohost", "url": "https:///path"},
            {"title": "creds", "url": "https://user:pw@example.com"},
            {"title": "quote", "url": 'https://example.com/"onmouseover=x'},
            {"title": "space", "url": "https://example.com/a b"},
            {"title": "ctrl\n", "url": "https://example.com"},
            {"title": "", "url": "https://example.com"},
            {"title": "x" * 81, "url": "https://example.com"},
            {"title": "long note", "url": "https://example.com", "note": "n" * 201},
            {"title": 5, "url": "https://example.com"},
            "https://example.com",
            None,
        ]
        self.assertEqual(dashboard._links({"links": bad}), [])

    def test_missing_or_wrong_type_config(self):
        self.assertEqual(dashboard._links({}), [])
        self.assertEqual(dashboard._links({"links": {"title": "x"}}), [])

    def test_limit(self):
        many = [{"title": f"L{i}", "url": f"https://e{i}.example.com"} for i in range(40)]
        self.assertEqual(len(dashboard._links({"links": many})), dashboard._LINK_LIMIT)

    def test_page_has_hidden_links_nav_and_escaping_renderer(self):
        self.assertIn('<nav id="links" class="links" aria-label="Links" hidden>', dashboard.PAGE)
        self.assertIn('href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"', dashboard.PAGE)
        self.assertIn("renderLinks(d.links);", dashboard.PAGE)


if __name__ == "__main__":
    unittest.main()
