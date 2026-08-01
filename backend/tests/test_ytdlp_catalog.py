from __future__ import annotations

import unittest

from app import server


class YtDlpCatalogTests(unittest.TestCase):
    def test_catalog_hides_cli_control_groups(self) -> None:
        catalog = server.ytdlp_option_catalog()
        group_titles = {group["title"] for group in catalog["groups"]}

        self.assertTrue(group_titles)
        self.assertTrue(group_titles.isdisjoint(server.YTDLP_CATALOG_HIDDEN_GROUPS))

    def test_catalog_hides_non_download_options_but_keeps_useful_options(self) -> None:
        catalog = server.ytdlp_option_catalog()
        flags = {
            flag
            for group in catalog["groups"]
            for option in group["options"]
            for flag in option["flags"]
        }

        self.assertTrue(flags.isdisjoint(server.YTDLP_CATALOG_HIDDEN_FLAGS))
        self.assertIn("--proxy", flags)
        self.assertIn("--cookies-from-browser", flags)
        self.assertIn("--format", flags)
        self.assertIn("--sub-langs", flags)

    def test_settings_keep_only_visible_ytdlp_options(self) -> None:
        config = "\n".join([
            "--proxy http://127.0.0.1:7890",
            "--cookies-from-browser chrome",
            "--exec echo ignored",
            "--write-link",
        ])

        self.assertEqual(
            server.filtered_ytdlp_settings_config(config),
            "--proxy http://127.0.0.1:7890\n--cookies-from-browser chrome",
        )

    def test_ffmpeg_ignores_options_not_exposed_by_the_settings_page(self) -> None:
        config = "\n".join([
            "-movflags +faststart",
            "-c:v libx264",
            "-crf 23",
            "-vn",
            "-f mp4",
        ])

        self.assertEqual(
            server.ffmpeg_config_tokens(config),
            ["-movflags", "+faststart", "-c:v", "libx264", "-crf", "23"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
