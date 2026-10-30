import unittest

from src.services.float_audio_service import FloatAudioOutputSink


class _FakeWin:
    def __init__(self, exists=True):
        self._exists = exists

    def winfo_exists(self):
        return self._exists


class _FakeFloatingWindow:
    def __init__(self, exists=True):
        self.win = _FakeWin(exists=exists)
        self.received = []

    def append_text(self, text):
        self.received.append(text)


class FloatAudioOutputSinkTests(unittest.TestCase):
    def test_appends_to_latest_float_when_available(self):
        latest = _FakeFloatingWindow(exists=True)
        sink = FloatAudioOutputSink(
            latest_window_getter=lambda: latest,
            create_window_fn=lambda: None,
        )

        appended = sink.append_text("hello")

        self.assertTrue(appended)
        self.assertEqual(latest.received, ["hello"])

    def test_creates_new_float_when_none_exists(self):
        created = _FakeFloatingWindow(exists=True)
        sink = FloatAudioOutputSink(
            latest_window_getter=lambda: None,
            create_window_fn=lambda: created,
        )

        appended = sink.append_text("hello")

        self.assertTrue(appended)
        self.assertEqual(created.received, ["hello"])

    def test_closed_target_float_rebinds_to_fresh_sink(self):
        stale = _FakeFloatingWindow(exists=True)
        fresh = _FakeFloatingWindow(exists=True)
        latest_values = [stale, fresh]

        def latest_window_getter():
            return latest_values[0]

        sink = FloatAudioOutputSink(
            latest_window_getter=latest_window_getter,
            create_window_fn=lambda: None,
        )

        first = sink.append_text("first")
        stale.win._exists = False
        latest_values[0] = fresh
        second = sink.append_text("second")

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(stale.received, ["first"])
        self.assertEqual(fresh.received, ["second"])

    def test_stale_latest_window_falls_back_to_creator(self):
        stale = _FakeFloatingWindow(exists=False)
        created = _FakeFloatingWindow(exists=True)
        sink = FloatAudioOutputSink(
            latest_window_getter=lambda: stale,
            create_window_fn=lambda: created,
        )

        appended = sink.append_text("hello")

        self.assertTrue(appended)
        self.assertEqual(stale.received, [])
        self.assertEqual(created.received, ["hello"])

    def test_append_text_returns_false_when_no_usable_window_exists(self):
        sink = FloatAudioOutputSink(
            latest_window_getter=lambda: None,
            create_window_fn=lambda: None,
        )

        appended = sink.append_text("hello")

        self.assertFalse(appended)

    def test_append_text_uses_custom_dispatch_function(self):
        latest = _FakeFloatingWindow(exists=True)
        dispatched = []

        def dispatch_fn(callback):
            dispatched.append("called")
            return callback()

        sink = FloatAudioOutputSink(
            latest_window_getter=lambda: latest,
            create_window_fn=lambda: None,
            dispatch_fn=dispatch_fn,
        )

        appended = sink.append_text("hello")

        self.assertTrue(appended)
        self.assertEqual(dispatched, ["called"])
        self.assertEqual(latest.received, ["hello"])


if __name__ == "__main__":
    unittest.main()
