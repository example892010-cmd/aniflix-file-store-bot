import unittest

from helper.anime_metadata import detect_auto_batch_metadata, build_auto_batch_result


class Media:
    def __init__(self, file_name):
        self.file_name = file_name


class Message:
    def __init__(self, file_name):
        self.document = Media(file_name)
        self.video = None
        self.audio = None
        self.photo = None
        self.animation = None
        self.voice = None
        self.video_note = None
        self.sticker = None
        self.caption = None
        self.text = None


class GetLinkAutoMetadataTests(unittest.TestCase):
    def test_detects_title_season_and_episode_variants(self):
        messages = [
            Message("@ANIFLIXANIMETAMIL - Dragon Ball Super Berus S01 Ep01 [480p] [Tamil-Hindi].mkv"),
            Message("Dragon Ball Super Berus S01 EP02 [720p].mkv"),
        ]
        title, episodes = detect_auto_batch_metadata(messages)
        self.assertEqual(title, "DRAGON BALL SUPER BEERUS")
        self.assertEqual(episodes, {1: {1, 2}})

    def test_season_padding_and_multi_season_order(self):
        messages = [
            Message("Anime Name S12 Ep03.mkv"),
            Message("Anime Name S02 E01.mkv"),
        ]
        title, episodes = detect_auto_batch_metadata(messages)
        self.assertEqual(title, "ANIME NAME")
        result = build_auto_batch_result(title, episodes, "https://t.me/example?start=batch_x")
        self.assertIn("Season 02\n01", result)
        self.assertIn("Season 12\n03", result)
        self.assertEqual(result.count("https://"), 1)

    def test_result_has_one_combined_link(self):
        result = build_auto_batch_result(
            "ANIME TITLE", {1: {1, 2, 10}}, "https://t.me/example?start=batch_x"
        )
        self.assertTrue(result.startswith("/batch1renamed\nANIME TITLE"))
        self.assertIn("Season 01\n01\n02\n10", result)
        self.assertEqual(result.count("https://"), 1)


if __name__ == "__main__":
    unittest.main()
