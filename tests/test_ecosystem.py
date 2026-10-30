import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsmon import ecosystem

SECRET = "sk-ant-THIS-MUST-NEVER-LEAK-1234567890abcdef"


class SshdTests(unittest.TestCase):
    def _root(self, main, includes):
        root = Path(tempfile.mkdtemp())
        (root / "etc/ssh/sshd_config.d").mkdir(parents=True)
        (root / "etc/ssh/sshd_config").write_text(main)
        for name, text in includes.items():
            (root / "etc/ssh/sshd_config.d" / name).write_text(text)
        return root

    def test_first_value_wins_across_sorted_includes(self):
        root = self._root("Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication no\nPermitRootLogin no\n",
                          {"01-cloud-init.conf": "PermitRootLogin yes\nPasswordAuthentication yes\n",
                           "60-cloudimg.conf": "PasswordAuthentication no\n"})
        eff = ecosystem.sshd_effective(root)
        self.assertEqual(eff["passwordauthentication"][0], "yes")
        self.assertIn("01-cloud-init.conf", eff["passwordauthentication"][1])
        self.assertEqual(eff["permitrootlogin"][0], "yes")

    def test_hardening_file_read_first_and_match_blocks_ignored(self):
        root = self._root("Include /etc/ssh/sshd_config.d/*.conf\nMatch User x\n  PasswordAuthentication yes\n",
                          {"00-hardening.conf": "PermitRootLogin no\nPasswordAuthentication no\n",
                           "01-cloud-init.conf": "PermitRootLogin yes\nPasswordAuthentication yes\n"})
        eff = ecosystem.sshd_effective(root)
        self.assertEqual(eff["passwordauthentication"][0], "no")
        self.assertEqual(eff["permitrootlogin"][0], "no")

    def test_checks_flag_password_and_root_login(self):
        checks = ecosystem.security_checks({"readable": True, "passwordauthentication": ("yes", "a"),
                                            "permitrootlogin": ("yes", "a")}, [], [], Path(tempfile.mkdtemp()))
        by = {c["id"]: c["status"] for c in checks}
        self.assertEqual(by["ssh_password"], "risk")
        self.assertEqual(by["ssh_root"], "risk")
        self.assertEqual(by["firewall"], "unknown")      # no ufw.conf in the fixture root

    def test_unreadable_config_is_unknown_not_ok(self):
        checks = ecosystem.security_checks({"readable": False}, [], [], Path(tempfile.mkdtemp()))
        self.assertEqual(checks[0]["status"], "unknown")

    def test_unreadable_include_does_not_report_safe_global_defaults(self):
        root = self._root('Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication no\n',
                          {'00-hidden.conf': 'PasswordAuthentication yes\n'})
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.name == '00-hidden.conf':
                raise PermissionError('fixture unreadable')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', read):
            parsed = ecosystem.sshd_effective(root)
        self.assertFalse(parsed['complete'])
        checks = ecosystem.security_checks(parsed, [], [], root)
        self.assertEqual(checks[0]['status'], 'unknown')
        self.assertNotIn('ssh_password', {check['id'] for check in checks})

    def test_unresolved_include_is_conservatively_unverified(self):
        root = self._root('Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication no\n', {})
        parsed = ecosystem.sshd_effective(root)
        self.assertFalse(parsed['complete'])


class NetworkTests(unittest.TestCase):
    def test_failed_listener_probe_is_unknown_but_successful_empty_scan_is_valid(self):
        with mock.patch.object(ecosystem, '_run', return_value=ecosystem.CommandOutput('', False)):
            observed = ecosystem.listeners()
        self.assertIsNone(observed)
        with mock.patch.object(ecosystem, '_active', return_value='unknown'):
            checks = ecosystem.security_checks({'readable': False}, observed, [], Path(tempfile.mkdtemp()))
        self.assertEqual(next(c for c in checks if c['id'] == 'public_ports')['status'], 'unknown')
        with mock.patch.object(ecosystem, '_run', return_value=ecosystem.CommandOutput('', True)):
            self.assertEqual(ecosystem.listeners(), [])
    def test_public_listener_other_than_ssh_is_warned(self):
        listen = [{"proto": "tcp", "address": "0.0.0.0", "port": "22", "process": "", "public": True},
                  {"proto": "udp", "address": "*", "port": "4000", "process": "cloudflared", "public": True},
                  {"proto": "tcp", "address": "0.0.0.0", "port": "8765", "process": "python3", "public": True}]
        checks = {c["id"]: c for c in ecosystem.security_checks({"readable": False}, listen, [], Path(tempfile.mkdtemp()))}
        self.assertEqual(checks["public_ports"]["status"], "warn")
        self.assertIn("8765", checks["public_ports"]["detail"])
        self.assertNotIn(":22 ", checks["public_ports"]["detail"])

    def test_ss_output_parsing(self):
        out = ('tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n'
               'tcp LISTEN 0 5 127.0.0.1:8765 0.0.0.0:* users:(("python3",pid=1,fd=3))\n'
               'tcp LISTEN 0 5 [::1]:631 [::]:*\n')
        with mock.patch.object(ecosystem, "_run", return_value=out):
            rows = ecosystem.listeners()
        self.assertEqual([(r["port"], r["public"]) for r in rows], [("22", True), ("631", False), ("8765", False)])
        self.assertEqual(rows[-1]["process"], "python3")

    def test_wildcard_udp_sockets_of_one_process_collapse(self):
        out = ('udp UNCONN 0 0 *:4001 *:* users:(("cloudflared",pid=1,fd=3))\n'
               'udp UNCONN 0 0 *:4002 *:* users:(("cloudflared",pid=1,fd=4))\n')
        with mock.patch.object(ecosystem, "_run", return_value=out):
            rows = ecosystem.listeners()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["port"], "4001, 4002")


class SecretSafetyTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".hermes").mkdir()
        (self.home / ".hermes/.env").write_text(f"# c\nGEMINI_API_KEY={SECRET}\nexport GITHUB_TOKEN='{SECRET}'\nEMPTY=\n")
        (self.home / ".config/agent2telegram").mkdir(parents=True)
        (self.home / ".config/agent2telegram/bot.json").write_text(json.dumps(
            {"agent": "codex", "token": SECRET, "bot_username": "demo_bot", "allowed_user_ids": [1],
             "elevenlabs_api_key": SECRET, "tmux_session": "T"}))
        (self.home / ".config/thing").mkdir()
        (self.home / ".config/thing/api-token").write_text(SECRET)
        for p in self.home.rglob("*"):
            if p.is_file():
                os.chmod(p, 0o600)
        os.chmod(self.home / ".config/thing/api-token", 0o644)

    def test_inventory_never_contains_secret_values(self):
        with mock.patch.object(ecosystem, "_home", return_value=self.home), \
             mock.patch.object(Path, "home", return_value=self.home):
            items = ecosystem.access_inventory()
        blob = json.dumps(items)
        self.assertNotIn(SECRET, blob)
        hermes = next(i for i in items if i["path"] == "~/.hermes/.env")
        self.assertEqual(hermes["keys"], ["GEMINI_API_KEY", "GITHUB_TOKEN"])
        bot = next(i for i in items if "demo_bot" in i["name"])
        self.assertIn("elevenlabs_api_key", bot["kind"])
        loose = next(i for i in items if i["path"] == "~/.config/thing/api-token")
        self.assertTrue(loose["exposed"])
        checks = {c["id"]: c for c in ecosystem.security_checks({"readable": False}, [], items, Path(tempfile.mkdtemp()))}
        self.assertEqual(checks["secret_files"]["status"], "risk")

    def test_redact(self):
        self.assertNotIn(SECRET, ecosystem.redact(f"curl -H 'Authorization: Bearer {SECRET}' x"))
        self.assertNotIn("abcdef1234567890", ecosystem.redact("token=abcdef1234567890"))
        self.assertEqual(ecosystem.redact("* * * * * /home/u/run.sh"), "* * * * * /home/u/run.sh")

    def test_cron_metadata_omits_credentials_in_arguments_and_environment(self):
        commands = [
            'curl -u demo:fixture-password https://example.test',
            'curl https://demo:fixture-password@example.test/health',
            'curl -H "Authorization: Basic ZGVtbzpwYXNz" https://example.test',
            'PASSWORD=fixture-password /home/u/run.sh --password short-secret',
            'env PASSWORD=fixture-password curl https://example.test',
        ]
        for command in commands:
            with self.subTest(command=command):
                result = ecosystem.cron_metadata('* * * * * ' + command)
                for secret in ('fixture-password', 'ZGVtbzpwYXNz', 'short-secret'):
                    self.assertNotIn(secret, result)
                self.assertIn('[arguments hidden]', result)
        self.assertEqual(ecosystem.cron_metadata('PASSWORD=fixture-password'), 'PASSWORD=[configured]')
        self.assertEqual(ecosystem.cron_metadata('@reboot /home/u/start.sh --token short'),
                         '@reboot start.sh [arguments hidden]')

    def test_git_remote_metadata_omits_userinfo_query_and_fragment(self):
        result = ecosystem.remote_metadata('https://demo:short-secret@github.com/a/b.git?token=small#password')
        self.assertEqual(result, 'https://github.com/a/b.git')
        self.assertEqual(ecosystem.remote_metadata('git@github.com:a/b.git'), 'github.com:a/b.git')
        self.assertEqual(ecosystem.remote_metadata('ext::helper --token short-secret'),
                         '[configured remote; details hidden]')


class CacheTests(unittest.TestCase):
    def test_refresh_is_rate_limited(self):
        calls = []
        with mock.patch.object(ecosystem, "collect", side_effect=lambda: calls.append(1) or {"n": len(calls)}):
            ecosystem._cache.update(at=0.0, data=None)
            ecosystem.snapshot()
            ecosystem.snapshot(refresh=True)          # too soon: served from cache
            self.assertEqual(len(calls), 1)
            ecosystem._cache["at"] -= ecosystem.MIN_RESCAN_SECONDS + 1
            ecosystem.snapshot(refresh=True)
            self.assertEqual(len(calls), 2)
        ecosystem._cache.update(at=0.0, data=None)


if __name__ == "__main__":
    unittest.main()


class EndpointAuthTests(unittest.TestCase):
    def test_ecosystem_endpoint_requires_dashboard_auth(self):
        import base64
        import threading
        import urllib.error
        import urllib.request
        from agentsmon import dashboard
        cfg = {"dashboard": {"auth": {"user": "u", "pwhash": dashboard.password_hash("pw")}}}
        server = dashboard.make_server("127.0.0.1", 0, cfg)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/ecosystem"
        try:
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(err.exception.code, 401)
            req = urllib.request.Request(url, headers={"Authorization": "Basic " + base64.b64encode(b"u:pw").decode()})
            with mock.patch.object(ecosystem, "snapshot", return_value={"security": {}}):
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(json.loads(resp.read()), {"security": {}})
                    self.assertEqual(resp.headers["Cache-Control"], "no-store")
        finally:
            server.shutdown()
            server.server_close()
