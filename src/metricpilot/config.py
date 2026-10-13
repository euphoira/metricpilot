import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    mode: str = "demo"
    model: str = ""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    data_dir: Path = field(default_factory=lambda: Path(".metricpilot"))
    max_steps: int = 8

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.mode not in {"demo", "live"}:
            raise ValueError("METRICPILOT_MODE must be demo or live")
        if self.mode == "live" and (not self.api_key or not self.model):
            raise ValueError("Live mode requires METRICPILOT_API_KEY and METRICPILOT_MODEL")
        if not 1 <= self.max_steps <= 12:
            raise ValueError("max_steps must be between 1 and 12")

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.getenv("METRICPILOT_MODE", "demo"),
            model=os.getenv("METRICPILOT_MODEL", ""),
            api_key=os.getenv("METRICPILOT_API_KEY", ""),
            base_url=os.getenv("METRICPILOT_BASE_URL", "https://api.openai.com/v1"),
            data_dir=Path(os.getenv("METRICPILOT_DATA_DIR", ".metricpilot")),
        )
