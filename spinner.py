"""Dot spinner with a status line, on stderr.

When stderr isn't a terminal (a log file), it writes the status every 15 seconds instead.
"""

import itertools
import shutil
import sys
import threading

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(self, text=""):
        self.text = text
        self._tty = sys.stderr.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        target = self._spin if self._tty else self._log
        self._thread = threading.Thread(target=target, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        if self._tty:
            self._clear()

    def write(self, line):
        """Print a line above the spinner without mangling it."""
        with self._lock:
            if self._tty:
                self._clear()
            print(line, flush=True)

    def _clear(self):
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _spin(self):
        for frame in itertools.cycle(FRAMES):
            if self._stop.wait(0.08):
                return
            width = shutil.get_terminal_size().columns - 3
            with self._lock:
                sys.stderr.write(f"\r\033[K\033[36m{frame}\033[0m {self.text[:width]}")
                sys.stderr.flush()

    def _log(self):
        last = None
        while not self._stop.wait(15):
            if self.text != last:
                last = self.text
                print(last, file=sys.stderr, flush=True)
