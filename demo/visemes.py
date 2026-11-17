"""The app's existing phoneme -> mouth-shape table. New rendering code is expected to reuse this."""

# Very rough letter -> viseme mapping, good enough for a cartoon mouth.
VISEMES = {
    "a": "open", "e": "wide", "i": "wide", "o": "round", "u": "round",
    "m": "closed", "b": "closed", "p": "closed",
    "f": "teeth", "v": "teeth",
    "w": "round", "l": "tongue", "t": "tongue", "d": "tongue", "n": "tongue",
}

REST = "rest"


def viseme_for(ch: str) -> str:
    return VISEMES.get(ch.lower(), REST)
