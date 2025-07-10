# test_dual_logger.py

import unittest
from io import StringIO
from autotune.dual_logger import DualLogger


class TestDualLogger(unittest.TestCase):
    def setUp(self):
        # Two fake file-like objects to test writing
        self.buf1 = StringIO()
        self.buf2 = StringIO()
        self.logger = DualLogger(self.buf1, self.buf2)

    def test_write_writes_to_all_files(self):
        msg = "hello world\n"
        self.logger.write(msg)
        self.assertEqual(self.buf1.getvalue(), msg)
        self.assertEqual(self.buf2.getvalue(), msg)

    def test_write_multiple_times(self):
        msgs = ["one\n", "two\n"]
        for m in msgs:
            self.logger.write(m)
        self.assertEqual(self.buf1.getvalue(), "".join(msgs))
        self.assertEqual(self.buf2.getvalue(), "".join(msgs))

    def test_flush_flushes_all(self):
        # StringIO doesn't buffer, so .flush() is a no-op, but we can still call it
        # Just test that it doesn't error
        try:
            self.logger.flush()
        except Exception as e:
            self.fail(f"Flush should not raise: {e}")

    def test_works_with_sys_stdout(self):
        # Should work if passed sys.stdout
        import sys

        logger = DualLogger(self.buf1, sys.stdout)
        logger.write("ok\n")
        self.assertTrue(self.buf1.getvalue().endswith("ok\n"))


if __name__ == "__main__":
    unittest.main()
