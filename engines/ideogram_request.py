"""The Ideogram sample-endpoint request payload as one typed value.

Captured from ideogram.ai's own call to ``/api/images/sample`` (2026-09):

    {"prompt": "...", "user_id": "...", "model_version": "AUTO",
     "model_uri": "model/AUTO/version/0", "use_autoprompt_option": "AUTO",
     "sampling_speed": 2, "character_reference_parents": [],
     "product_reference_parents": [], "resolution": {"width": 1280, "height": 800},
     "num_images": 4, "style_type": "AUTO"}

The engine builds its payload from this instead of a hand-written dict, so the
wire shape lives in one place and nonsense values fail here rather than at the
API. It is a plain dataclass on purpose: the engines bundle carries no pydantic.
"""

from dataclasses import dataclass, field

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
DEFAULT_NUM_IMAGES = 4
MIN_SAMPLING_SPEED = 0
MAX_SAMPLING_SPEED = 3
MAX_NUM_IMAGES = 8


class InvalidImageRequest(ValueError):
    """The request would be rejected locally, before any API call."""


@dataclass(frozen=True)
class Resolution:
    """Output resolution; the API takes width/height in pixels."""

    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvalidImageRequest(
                f"resolution must be positive, got {self.width}x{self.height}"
            )

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class IdeogramImageRequest:
    """One image-generation request to the Ideogram sample endpoint."""

    prompt: str
    user_id: str
    model_version: str = "AUTO"
    use_autoprompt_option: str = "AUTO"
    sampling_speed: int = 2
    style_type: str = "AUTO"
    num_images: int = DEFAULT_NUM_IMAGES
    resolution: Resolution = field(default_factory=Resolution)
    character_reference_parents: list[str] = field(default_factory=list)
    product_reference_parents: list[str] = field(default_factory=list)
    model_uri: str | None = None  # derived from model_version when omitted

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise InvalidImageRequest("prompt must not be empty")
        if not self.user_id or not self.user_id.strip():
            raise InvalidImageRequest("user_id must not be empty")
        if not 1 <= self.num_images <= MAX_NUM_IMAGES:
            raise InvalidImageRequest(
                f"num_images must be between 1 and {MAX_NUM_IMAGES}, "
                f"got {self.num_images}"
            )
        if not MIN_SAMPLING_SPEED <= self.sampling_speed <= MAX_SAMPLING_SPEED:
            raise InvalidImageRequest(
                f"sampling_speed must be between {MIN_SAMPLING_SPEED} and "
                f"{MAX_SAMPLING_SPEED}, got {self.sampling_speed}"
            )
        for name in ("model_version", "use_autoprompt_option", "style_type"):
            if not str(getattr(self, name)).strip():
                raise InvalidImageRequest(f"{name} must not be empty")
        # Keep model_uri consistent with model_version instead of drifting.
        if self.model_uri is None:
            object.__setattr__(
                self, "model_uri", f"model/{self.model_version}/version/0"
            )

    def to_payload(self) -> dict:
        """The exact JSON body for ``/api/images/sample`` (fresh lists each call)."""
        return {
            "prompt": self.prompt,
            "user_id": self.user_id,
            "model_version": self.model_version,
            "model_uri": self.model_uri,
            "use_autoprompt_option": self.use_autoprompt_option,
            "sampling_speed": self.sampling_speed,
            "character_reference_parents": list(self.character_reference_parents),
            "product_reference_parents": list(self.product_reference_parents),
            "resolution": self.resolution.to_dict(),
            "num_images": self.num_images,
            "style_type": self.style_type,
        }
