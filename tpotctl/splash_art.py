"""The T-Pot ANSI logo as pixels: the honey pot, the honeycombs and the T-Pot lettering.

Taken from the elite BBS ANSI template of the logo (ansi/, kept out of git): t-pot-animate.py
(80 x 62 and 120 x 94 pixels) and 80x24/t-pot-animate-80x24.py (80 x 48 pixels, set tighter for
a terminal of 80 x 24). Two pixels make one character (half blocks), every pixel is an index into
PALETTE. The grids are zlib + base64 as in the template. Standard library only.
"""

import base64
import zlib
from functools import lru_cache
from typing import List, Tuple

# the colours of the template: 0 black, 1-6 magenta from dark to light (4 is Telekom magenta),
# 7 the white of the highlights, 8 and 9 the greys of the outlines
PALETTE = (
    (0, 0, 0), (55, 0, 28), (104, 0, 53), (162, 0, 83), (226, 0, 116),
    (255, 76, 167), (255, 157, 208), (245, 240, 243), (91, 88, 90), (172, 168, 171),
)

GRIDS = {
    "120": (120, 94, (
        "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"
        "ODYpxb/w7leTXo2vK/fJtQn48xn+A/APO24="
    )),
    "80": (80, 62, (
        "eNrdWFuCmzAM9EhOV9z/wtXLtkwgSdN+lX40GBiPHh5J29r/cBExMTN9/f1+O4DoS0DameQd9AH+BT82LPvX8CUeTnSR17f8"
        "FjDZNeCgHqSvmUFD6oQQqI481j685JDCLMD0F5eL8sEHSaZfS74KRmLRDsfcmfp7QOjGjGEvUXev6Vp/vui9zUpOP595x4rf"
        "zThnuGM+ugXmXRybB5AHHkTcheEyLmBKTZ99htd4Wg+pz3sPSDdAncx4i9cqHp/ywvYzPOf1gTSc+Onr8PzjmXkK0zWsthEc"
        "83VwHaHly3ojh7PDOhqKx+MEqzP4BaCxtwAbqALq3sePuAigKAD15VKNyK0Lkcc9dtQzr7H4OU7kFp7lZcOxcuGJXdvFQ43Z"
        "4XZ+sSLHpq54gaf/G95Eu8Bzgo2rot3j6WsHJpFyhB7hv1hX4eAt3RYe8mwgNIUjuDjj4UFtM1hwVy42Pw0Ln8R93gfBWzzX"
        "Fqj0xXXYjwMrNcpVkPUM5zcXsPpINpnDMeHWOiqg+FZ2hI4rnkK7KOWuCvdYq9g+VWa/YvUKbxc5nniPurzZlnB9k6JRaG7x"
        "+glP/euX6mM+g/lwqIZpgbuGphAHfqaFhOwRJZ7IIKAqmHszuSM3EXM+sWHcR+IEHrmkBpXhZw1DsUZqtrjuaujTADVEpoux"
        "47XlFlouYud3CjOQzrLADTfB8XhQB9dop8Fm76lDiRJ9DDysjMMWpWCrBzNYT/8I6KmlIiy8SeESL9kmHjeIXLZoB73EMw9d"
        "4PV2dUAWnhlpIbKCPvCydWnX/CwXX+NleiKpcsqqjOgcFU+zT0q9Q8FLUtMfkvHFrELBNms7rXyZJT5/IPEiqCZEeqTUMYmX"
        "ciW8cs7xvOFyvCHXgeetUOLVbNKUrEtWd92G7JVGTyjmomKvSrRV3WxvNzz2Rm7KQ1sdQ7ZMeZhLiwT2Zo8ldd8/9qrS8vjP"
        "LURm3XMHzC9quZuVcbAA5mJGRoqIxrpIvWm15QKNihmndjS7UWhjaRaorZbkT+9ET3h6T9kDRfd0Lph78YzeiHLKYXfPmV/U"
        "TVPZUZNPJXh6xl/QzMq2FtETUO2eI1bN4RrLc/9SV8Bt8LvCi3xBRLqOC1udLxvMiXPhta1JH609UP1DCxFlhltzQGRa6ufL"
        "IYK5bQwr2Vp4KHrQu8kOeUA528satfFbo0954Kpld82MVwV9iW86WgtrVB+8nuSs1Y0BI0Qz1SE5rYEwZk9TmNsJe5X3Fm/m"
        "jY9IazzK6I8JMfLw1diAgTfHmTkS+sIMMvmWdTy5psk+L/RIgzUp9JyL4uPQ66fDczdpUuyM/NpQymQcc/ufDf1hEzPGkKTN"
        "cxbMv/xjxB6xv/gT0cmr7C54/+5vIT0b9g=="
    )),
    "80x24": (80, 48, (
        "eNrFVwlSIzEMtKTJRv7/h1e35cEhwFK1BioTj926W2KM/7HA14/v4+0rOh7+EBEJd7h6gt/QjyDtRfgVz4W59vnd++1RzDbP"
        "yR/Gsr1vYPLkchqqMgqH1JdJ+YrPTDBD6gbD1aL7ui6gtyqab7DOkTxTGokCsS21+x2ce54Kj3lAagcNkMQK5i/ijcQDQt5S"
        "hlxJjwXjO4NveEjhvMwUDY0AojrCdj8HBJFvCUZ2TsqDJ7icxBQhl8Uo0orwEzTUi/qrn1qu8wnFApnDeHFlqHj3pYZpk0u0"
        "oMwnf4ArPN2B+RqQxs4eouFzjs/xBHAFT/0Nr/Hk2Ez1NjsehQd2BHs47+wmXrSQComod+zi3c1QHKGVmbV04jvVjre1Srqt"
        "DXDklWPxMm+VCrPg1j7UXcNTUUoQ84TIvNdo6ML8eLTNzUSBs3clO2jFcpb3oqfCu0uBsj3gBI+TIbMEBA+dixRLmWXDq1fc"
        "vAxJE3KevZAWp3tMXRnb04zRH9tSua6KuDNJi6FZwy25PE1MpuP1MEPg+WXAPwsEsOFtbSB0nYlXVkEoEeZe4BQY5BX2qHUf"
        "CFFoABZeCoZmlKkU2nLDo3HKQcWfGO/PeEpmR7z5ooUHnoBEDmPiBQkyn/BA+R9vLTfw6LIygEpZ9xhYGijrLW8Kv7VYL3oK"
        "3jO8KKsVDxA8MhU8hVevI8WLBqr5UvSFjio6TPQe2arKKtRE2Nc5WjXLZvZ4xcMbZSmOkxsb2Q9P8yifEMGwlqVnPnZ6rYGH"
        "/Tg7mD8HzoG++pfR8xlqXoyX6tLk+sVxXlHQ2TylWiuD2zirsNFuaG2eJjhrMtYS4xjt7cHTywjHRDUFO+LaMe7XAXPDw96C"
        "k7xWr4ANsX+rVlK9Fna8bZD0Lodjs7nDIfXMMJNs6DwOMpFgcGH1HshPyOkmSB28HuDlrO5qk41ldB4AdD9HpZeTakyiHnej"
        "YA+N5r4rXUWmepjIx0W3Tn6Y2sRxD7sGZRytSbIahAmy0RqrEZ1naPTDhK2wdPaFaoUo6kMl4vv/GNbYHFlJKh5L5T5nf/Pf"
        "rjY0ejslHP++lsbv1foLr7AW5Q=="
    )),
}

# where the template's effects happen, in the coordinates of its design (1236 x 960 from x 8, y 100)
DROPS = ((565, 824), (695, 832), (790, 824))          # the three drops under the lettering
STARS = ((264, 251), (357, 172), (1104, 438), (59, 735), (950, 835))
SYRUP_X, SYRUP_Y = 686, range(340, 465, 10)            # the long drip above the lettering
POOL_Y = 964                                           # where the drops land


@lru_cache(maxsize=None)
def grid(variant: str) -> Tuple[int, int, List[int]]:
    """Width and height in pixels and the pixels, row by row."""
    width, height, chunks = GRIDS[variant]
    return width, height, list(zlib.decompress(base64.b64decode("".join(chunks))))


def design_xy(width: int, height: int, x: float, y: float) -> Tuple[int, int]:
    """A point of the design as the pixel of a grid (the template's coords())."""
    return round((x - 8) / 1236 * width), round((y - 100) / 960 * height)


def design_of(width: int, height: int, x: int, y: int) -> Tuple[float, float]:
    """The point of the design a pixel shows (its centre)."""
    return 8 + (x + .5) / width * 1236, 100 + (y + .5) / height * 960
