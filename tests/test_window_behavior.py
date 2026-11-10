import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from videowatcher.app import Window


class WindowBehaviorTests(unittest.TestCase):
    def test_close_requests_application_exit(self):
        app = SimpleNamespace(quit=Mock())
        window = SimpleNamespace(app=app)
        self.assertFalse(Window.close_window(window))
        app.quit.assert_called_once_with()

    def test_background_action_hides_without_quitting(self):
        app = SimpleNamespace(quit=Mock())
        window = SimpleNamespace(app=app, set_visible=Mock())
        Window.hide_window(window)
        window.set_visible.assert_called_once_with(False)
        app.quit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
