import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from updater import (
    clean_repo_slug,
    parse_version,
    is_newer_version,
    fetch_latest_release,
    extract_update_zip,
    create_restart_script,
)


class UpdaterTests(unittest.TestCase):
    def test_clean_repo_slug(self):
        cases = [
            ('owner/landesk', 'owner/landesk'),
            ('https://github.com/owner/landesk', 'owner/landesk'),
            ('https://github.com/owner/landesk/', 'owner/landesk'),
            ('http://github.com/my-org/my-repo', 'my-org/my-repo'),
            ('github.com/user/project', 'user/project'),
            ('', ''),
            ('invalid', 'invalid'),
        ]
        for inp, expected in cases:
            self.assertEqual(clean_repo_slug(inp), expected)

    def test_version_parsing_and_comparison(self):
        self.assertEqual(parse_version('0.3.0'), (0, 3, 0))
        self.assertEqual(parse_version('v0.3.1'), (0, 3, 1))
        self.assertEqual(parse_version('V1.2.3'), (1, 2, 3))
        self.assertEqual(parse_version('2.0'), (2, 0, 0))
        self.assertEqual(parse_version('invalid'), (0, 0, 0))

        self.assertTrue(is_newer_version('0.3.1', '0.3.0'))
        self.assertTrue(is_newer_version('v0.3.1', '0.3.0'))
        self.assertTrue(is_newer_version('v1.0.0', '0.3.0'))
        self.assertTrue(is_newer_version('0.4.0', '0.3.9'))
        self.assertFalse(is_newer_version('0.3.0', '0.3.0'))
        self.assertFalse(is_newer_version('v0.2.0', '0.3.0'))
        self.assertFalse(is_newer_version('0.3.0', '0.3.1'))

    def test_fetch_latest_release_parses_json(self):
        mock_response_data = {
            'tag_name': 'v0.3.1',
            'name': 'LanDesk v0.3.1 Release',
            'body': '- Bug fixes and improvements\n- Better performance',
            'html_url': 'https://github.com/test-org/landesk/releases/tag/v0.3.1',
            'assets': [
                {
                    'name': 'LanDesk-v0.3.1-source.zip',
                    'browser_download_url': 'https://github.com/test-org/landesk/releases/download/v0.3.1/LanDesk-v0.3.1-source.zip',
                    'size': 12345,
                },
                {
                    'name': 'LanDesk-v0.3.1-Windows-x64.zip',
                    'browser_download_url': 'https://github.com/test-org/landesk/releases/download/v0.3.1/LanDesk-v0.3.1-Windows-x64.zip',
                    'size': 23000000,
                },
            ],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode('utf-8')
        mock_resp.headers.get_content_charset.return_value = 'utf-8'
        mock_opener = MagicMock()
        mock_opener.return_value.__enter__.return_value = mock_resp

        rel = fetch_latest_release('test-org/landesk', opener=mock_opener)
        self.assertEqual(rel['tag'], 'v0.3.1')
        self.assertEqual(rel['version'], '0.3.1')
        self.assertEqual(rel['title'], 'LanDesk v0.3.1 Release')
        self.assertIn('Bug fixes', rel['body'])
        self.assertEqual(rel['asset_name'], 'LanDesk-v0.3.1-Windows-x64.zip')
        self.assertEqual(rel['asset_size'], 23000000)
        self.assertIn('Windows-x64.zip', rel['download_url'])

    def test_extract_and_restart_script(self):
        tmp_base = Path(tempfile.gettempdir()) / f'landesk_test_up_{os.getpid()}'
        tmp_base.mkdir(parents=True, exist_ok=True)
        try:
            zip_file = tmp_base / 'package.zip'
            with zipfile.ZipFile(zip_file, 'w') as zf:
                zf.writestr('LanDesk/LanDesk.exe', b'MZFakeBinary')
                zf.writestr('LanDesk/README.md', b'New version readme')
                zf.writestr('LanDesk/_internal/lib.dll', b'DLL')

            staging_dir = tmp_base / 'staging'
            extracted = extract_update_zip(zip_file, staging_dir)
            self.assertTrue(extracted.exists())
            self.assertTrue((extracted / 'LanDesk.exe').exists())
            self.assertTrue((extracted / 'README.md').exists())

            app_dir = tmp_base / 'app'
            app_dir.mkdir(parents=True, exist_ok=True)
            script_path = create_restart_script(extracted, app_dir, 'LanDesk.exe')
            self.assertTrue(script_path.exists())
            script_content = script_path.read_text(encoding='ansi')
            self.assertIn('robocopy', script_content)
            self.assertIn('start ""', script_content)
            self.assertIn('LanDesk.exe', script_content)
            script_path.unlink(missing_ok=True)
        finally:
            shutil.rmtree(tmp_base, ignore_errors=True)

    def test_installer_copy_excludes_dpapi_and_creates_uninstaller(self):
        from installer import install_landesk
        tmp_base = Path(tempfile.gettempdir()) / f'landesk_test_inst_{os.getpid()}'
        src_dir = tmp_base / 'src'
        dst_dir = tmp_base / 'dst'
        src_dir.mkdir(parents=True, exist_ok=True)
        try:
            (src_dir / 'LanDesk.exe').write_bytes(b'MZFakeExe')
            (src_dir / 'settings-v2.dat').write_bytes(b'SecretDPAPI')
            (src_dir / 'identity.key').write_bytes(b'SecretKey')
            (src_dir / 'README.md').write_text('Readme', encoding='utf-8')
            with patch('installer.register_uninstaller'), patch('installer.create_shortcut'):
                installed = install_landesk(source_dir=src_dir, install_dir=dst_dir, desktop_shortcut=True, start_menu_shortcut=True)
            self.assertEqual(installed, dst_dir.resolve())
            self.assertTrue((dst_dir / 'LanDesk.exe').exists())
            self.assertTrue((dst_dir / 'Uninstall-LanDesk.exe').exists())
            self.assertTrue((dst_dir / 'README.md').exists())
            self.assertFalse((dst_dir / 'settings-v2.dat').exists())
            self.assertFalse((dst_dir / 'identity.key').exists())
        finally:
            shutil.rmtree(tmp_base, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
