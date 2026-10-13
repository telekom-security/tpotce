"""A command at a real terminal (a pty) for the tests: what it prints, its exit code, nothing left running.

One reader for test_ui.at_terminal (a bash script of the ui helpers, a terminal size, keys after a second)
and test_scripts.at_terminal (a command, answers typed once their question is in the output).
"""

import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import time


def run(argv, env, cwd=None, size=None, onlcr=True, stdout_tty=True, answers=(), keys=None, keys_after=1.0,
        timeout=60):
    """argv with stdin and stderr on a pty, stdout too unless stdout_tty is False (then a pipe):
    (exit code, the bytes of the pty, the text of the pipe).

    size: (cols, rows) of the pty. onlcr=False keeps a \\n a \\n (a CR of the script shows).
    answers: (text, keys) in their order, the keys are typed once the text is in the output (after the
    text of the answer before). keys: typed once, keys_after seconds after the start.
    The deadline (timeout) ends the reading and kills the command (the code is that of the kill). A
    command that is gone leaves what it wrote last in the pty: it is read on, until the pty is quiet or
    closed; a child of the command may keep it open and write on, so this reading ends at the deadline
    (+2 s) at the latest."""
    master, slave = pty.openpty()
    if size:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[1], size[0], 0, 0))
    if not onlcr:
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.ONLCR
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=slave, stdout=slave if stdout_tty else subprocess.PIPE,
                            stderr=slave, close_fds=True)
    os.close(slave)
    out, seen, waiting = b"", 0, list(answers)
    started = time.time()
    deadline = started + timeout

    def chunk(wait):
        """What the pty has within wait seconds: bytes, b"" for nothing yet, None at its end (EOF, EIO)."""
        ready, _w, _x = select.select([master], [], [], wait)
        if not ready:
            return b""
        try:
            data = os.read(master, 65536)
        except OSError:                             # Linux: EIO once the command is gone
            return None
        return data or None

    try:
        while time.time() < deadline:
            if keys and time.time() >= started + keys_after:
                os.write(master, keys)
                keys = None
            if waiting and waiting[0][0].encode() in out[seen:]:
                text, typed = waiting.pop(0)
                seen = out.index(text.encode(), seen) + len(text.encode())
                os.write(master, typed)
            data = chunk(0.1)
            if data is None:
                break
            if data:
                out += data
            elif proc.poll() is not None:
                # gone: drain what is left, until quiet or closed, the deadline (+2 s) at the latest
                while time.time() < deadline + 2:
                    data = chunk(0.2)
                    if not data:
                        break
                    out += data
                break
        else:
            proc.kill()                             # the deadline: the output so far, the code of the kill
        code = proc.wait(timeout=10)
        piped = ""
        if proc.stdout:
            piped = proc.stdout.read().decode("utf-8", "replace")
        return code, out, piped
    finally:
        if proc.poll() is None:                     # the deadline, or a wait that timed out: no command stays
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()
        os.close(master)
