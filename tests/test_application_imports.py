import unittest


class ApplicationImportsTest(unittest.TestCase):
    def test_application_entrypoints_importable(self) -> None:
        import xbot.__main__
        import xbot.main
        import xbot.bot.application
        from xbot.bot.application import build_application, main
        from xbot.bot.handlers import main as legacy_main

        self.assertTrue(callable(xbot.main.main))
        self.assertTrue(callable(xbot.bot.application.main))
        self.assertTrue(callable(build_application))
        self.assertTrue(callable(main))
        self.assertTrue(callable(legacy_main))


if __name__ == "__main__":
    unittest.main()
