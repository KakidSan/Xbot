import unittest


class ApplicationImportsTest(unittest.TestCase):
    def test_application_entrypoints_importable(self) -> None:
        import xbot.main
        import xbot.bot.application
        from xbot.bot.handlers import main_menu

        self.assertTrue(callable(xbot.main.main))
        self.assertTrue(callable(xbot.bot.application.main))
        self.assertTrue(callable(main_menu.handle_main_menu_callback))


if __name__ == "__main__":
    unittest.main()
