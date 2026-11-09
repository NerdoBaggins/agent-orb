import numpy as np

from orb.audio import SAMPLE_RATE

GAP_S = 0.02
LOSSY_SHARE = 0.1


def lost_share(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], (samples == 0).astype(np.int8), [0]))))
    runs = edges[1::2] - edges[::2]
    return float(runs[runs >= GAP_S * SAMPLE_RATE].sum() / samples.size)


def gap_notice(lost: float) -> str:
    return f"your mic audio is arriving with gaps ({lost:.0%} of it lost); server capture avoids this"
