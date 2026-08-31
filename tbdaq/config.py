from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GatorConfig:
    enabled: bool = True
    binary_path: str = ""
    library_path: str = ""   # directory containing libgtrlib .so (for LD_LIBRARY_PATH)
    channel: int = 8
    samplerate: Optional[int] = None     # 1000 | 5000 | 10000 | 19000
    fullscale: Optional[int] = None      # 8–127
    threshold: Optional[float] = None   # 0.0–1.0


@dataclass
class EndaqConfig:
    enabled: bool = True
    ide2csv_path: Optional[str] = None   # Path to ide2csv binary; None = skip conversion


@dataclass
class SessionConfig:
    output_root: str = "./csv-output"
    run_count: int = 1
    run_duration_s: Optional[float] = None   # None = manual stop
    run_period_s: Optional[float] = None     # start-to-start interval; None = back-to-back
    gator: GatorConfig = field(default_factory=GatorConfig)
    endaq: EndaqConfig = field(default_factory=EndaqConfig)
