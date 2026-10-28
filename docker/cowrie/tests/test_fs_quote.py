"""The quoting of names by the persona's rm, cp, mv, touch, rmdir and mkdir against the recordings of the real
tools (fixtures/fs_quote.json): names with quotes, shell characters, control characters, bytes of no UTF-8 character
and letters beyond ASCII, in GNU coreutils 8.22 to 9.7 (quoteaf, and quote() in the two messages that keep it) and
in BusyBox (the bytes as they are), and the dates touch -d takes or refuses (a newline around a letter, an empty
date), each case run through the patched wc.py on Cowrie's file system as tests.test_fs_tools runs the recorded
cases."""
import json
import unittest
from pathlib import Path

from tests.test_fs_tools import REFS as FS_REFS, Shell, T_DIR, T_FILE, keys_of, make_fs, node

REFS = json.loads((Path(__file__).parent / "fixtures" / "fs_quote.json").read_text(encoding="utf-8"))["images"]
# label -> (what the name is before the case, the words; NAME stands for the name)
CASES = {
    "rm -v": ("file", ["rm", "-v", "--", "NAME"]),
    "rm missing": (None, ["rm", "--", "NAME"]),
    "mkdir twice": (None, ["mkdir", "--", "NAME"]),
    "cp -v": ("file", ["cp", "-v", "--", "NAME", "c1"]),
    "mv -v": ("file", ["mv", "-v", "--", "NAME", "m1"]),
    "touch -d": (None, ["touch", "-d", "NAME", "f"]),
    "rm -i": ("file", ["rm", "-i", "--", "NAME"]),
    "rmdir": (None, ["rmdir", "--", "NAME"]),
    "mkdir -v": (None, ["mkdir", "-v", "--", "NAME"]),
    "rm -rv dir": ("dir", ["rm", "-rv", "--", "NAME"]),
    "cp dir": ("dir", ["cp", "--", "NAME", "c2"]),
    "mv -T": ("dir", ["mv", "-T", "--", "a", "NAME"]),
    "rmdir -v": ("dir", ["rmdir", "-v", "--", "NAME"]),
    "cp same": ("file", ["cp", "--", "NAME", "NAME"]),
    "cp -v b2": ("file", ["cp", "-v", "--", "NAME", "b2"]),
    "mv -v b3": ("file", ["mv", "-v", "--", "NAME", "b3"]),
    "rmdir dir": ("dir", ["rmdir", "--", "NAME"]),
}


class FsQuoteTest(unittest.TestCase):
    maxDiff = None

    def test_every_recorded_name(self):
        count = 0
        for key, image in REFS.items():
            shell = Shell(keys_of(key, FS_REFS[key]))
            for name_json, body in image["cases"].items():
                name = json.loads(name_json)
                for label, result in body.items():
                    before, words = CASES[label]
                    fs = make_fs({"r": [], "bin": []})
                    w = fs.getfile("/tmp/w")[7]
                    if before == "file":
                        w.append(node(name, T_FILE, 0o100644))
                    elif before == "dir":
                        w.append(node(name, T_DIR, 0o40755))
                    words = [name if word == "NAME" else word for word in words]
                    with self.subTest(image=key, name=name_json, case=label):
                        out, err, status = shell.run(fs, words, 0, b"" if label == "rm -i" else None)
                        if label == "mkdir twice":
                            out2, err2, status = shell.run(fs, words, 0, None)
                            out, err = out + out2, err + err2
                        self.assertEqual([out, err, status], result)
                        if label == "touch -d":
                            # a date touch took makes f (the `; ls x` runs of the recording listed x exactly then)
                            self.assertEqual(fs.getfile("/tmp/w/f") is not None, status == 0)
                    count += 1
        self.assertGreater(count, 3000)


if __name__ == "__main__":
    unittest.main()
