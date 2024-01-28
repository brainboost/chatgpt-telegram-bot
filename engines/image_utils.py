import base64
import re
from io import BytesIO
from typing import IO, Union

try:
    from PIL.Image import Image
    from PIL.Image import new as new_image
    from PIL.Image import open as open_image
except ImportError:
    Image = type

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

ImageType = Union[str, bytes, IO, Image, None]


def is_data_uri_an_image(data_uri: str) -> bool:
    if not re.match(r"data:image/(\w+);base64,", data_uri):
        raise ValueError("Invalid data URI image.")
    image_format = re.match(r"data:image/(\w+);base64,", data_uri).group(1)
    if image_format.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Invalid image format (from mime file type).")


def extract_data_uri(data_uri: str) -> bytes:
    data = data_uri.split(",")[1]
    data = base64.b64decode(data)
    return data


def to_image(image: ImageType) -> Image:
    if isinstance(image, str):
        is_data_uri_an_image(image)
        image = extract_data_uri(image)
    if isinstance(image, bytes):
        # is_accepted_format(image)
        return open_image(BytesIO(image))
    elif not isinstance(image, Image):
        image = open_image(image)
        copy = image.copy()
        copy.format = image.format
        return copy
    return image


def to_base64_jpg(image: Image, compression_rate: float) -> str:
    output_buffer = BytesIO()
    image.save(output_buffer, format="JPEG", quality=int(compression_rate * 100))
    return base64.b64encode(output_buffer.getvalue()).decode()


def process_image(img: Image, new_width: int, new_height: int) -> Image:
    img.thumbnail((new_width, new_height))
    # Remove transparency
    if img.mode != "RGB":
        img.load()
        white = new_image("RGB", img.size, (255, 255, 255))
        white.paste(img, mask=img.split()[3])
        return white
    return img


def format_images_markdown(
    images, alt: str, preview: str = "{image}?w=200&h=200"
) -> str:
    """Formats the given images as a markdown string."""
    if isinstance(images, str):
        images = f"[![{alt}]({preview.replace('{image}', images)})]({images})"
    else:
        images = [
            f"[![#{idx+1} {alt}]({preview.replace('{image}', image)})]({image})"
            for idx, image in enumerate(images)
        ]
        images = "\n".join(images)
    start_flag = "<!-- generated images start -->\n"
    end_flag = "<!-- generated images end -->\n"
    return f"\n{start_flag}{images}\n{end_flag}\n"


class ImageResponse:
    def __init__(self, images: Union[str, list], alt: str, options: dict = {}):
        self.images = images
        self.alt = alt
        self.options = options

    def __str__(self) -> str:
        return format_images_markdown(self.images, self.alt)

    def get(self, key: str):
        return self.options.get(key)


class ImageRequest(ImageResponse):
    pass
