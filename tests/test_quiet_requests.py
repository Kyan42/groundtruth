"""The one harmless asyncio traceback Playwright causes when a page moves on mid-request is dropped; nothing else is."""
import asyncio
import logging
import unittest

import toolserver.catalog  # noqa: F401  (installs the filter)


class Quiet(unittest.TestCase):
    def records(self, emit):
        seen = []

        class Keep(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        log, h = logging.getLogger("asyncio"), Keep()
        log.addHandler(h)
        try:
            emit(log)
        finally:
            log.removeHandler(h)
        return seen

    def test_a_cancelled_request_check_is_not_printed(self):
        def emit(log):
            try:
                raise asyncio.CancelledError()
            except asyncio.CancelledError as e:
                log.error("Exception in callback Connection.dispatch.<locals>._done_callback(<Task cancelled>)", exc_info=(type(e), e, e.__traceback__))
        self.assertEqual(self.records(emit), [])

    def test_everything_else_still_is(self):
        def emit(log):
            try:
                raise ValueError("boom")
            except ValueError as e:
                log.error("Exception in callback Connection.dispatch.<locals>._done_callback(<Task>)", exc_info=(type(e), e, e.__traceback__))
            try:
                raise asyncio.CancelledError()
            except asyncio.CancelledError as e:
                log.error("Exception in callback something_else()", exc_info=(type(e), e, e.__traceback__))
            log.error("Task was destroyed but it is pending!")
        self.assertEqual(len(self.records(emit)), 3)


if __name__ == "__main__":
    unittest.main()
