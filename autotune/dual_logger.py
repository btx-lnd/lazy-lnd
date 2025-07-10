# dual_logger.py


class DualLogger:
    def __init__(self, *files):
        self.files = files

    def write(self, msg):
        for f in self.files:
            f.write(msg)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()
