"""Golden two-color enclosure badge split from one parent solid."""
from build123d import Align, Box, Pos
from amagine_cad import observe_feature, publish_color_model, subtract_checked

WIDTH = 60.0
DEPTH = 40.0
HEIGHT = 5.0
SPLIT_X = 0.0

parent = Box(WIDTH, DEPTH, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
left_tool = Pos(-WIDTH / 4, 0, 0) * Box(
    WIDTH / 2,
    DEPTH + 2,
    HEIGHT + 2,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
accent = parent & left_tool
body = subtract_checked(parent, left_tool, "color-split")
observe_feature(body, "body-region", "color-region")
observe_feature(accent, "accent-region", "color-region")

if __name__ == "__main__":
    publish_color_model(
        {"body": (body, "#20242b"), "accent": (accent, "#ff5a36")},
        "multi-enclosure",
        out_dir="cad_out",
    )
