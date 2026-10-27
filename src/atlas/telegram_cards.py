"""Small local PNG renderer with local fonts and no remote assets."""

from functools import cache
from io import BytesIO
from pathlib import Path

from atlas.activity import ActivityEvent, EventType
from atlas.telegram_presentation import bounded, describe

CARD_EVENTS = frozenset(
    {
        EventType.FILL,
        EventType.PARTIAL,
        EventType.CLOSED,
        EventType.BREACH,
        EventType.KILL,
        EventType.ERROR,
    }
)


def _font_file():
    # Read existing fonts only; no fonts are downloaded, copied or redistributed.
    for path in ("C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            return path
    return None


def render_card(event: ActivityEvent, *, fictional=False) -> bytes:
    # Optional dependency: normal HTML-only operation does not import Pillow.
    from PIL import Image, ImageDraw, ImageFont

    event = ActivityEvent.model_validate(event.model_dump())
    notice = describe(event)
    image = Image.new("RGB", (1080, 1120), "#080f1e")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (32, 32, 1048, 1088), radius=36, fill="#101d31", outline="#253650", width=2
    )
    draw.rounded_rectangle((32, 32, 1048, 46), radius=7, fill=notice.accent)

    font_file = _font_file()

    @cache
    def font(size):
        return (
            ImageFont.truetype(font_file, size=size)
            if font_file
            else ImageFont.load_default(size=size)
        )

    def text(value, xy, *, size=28, fill="#edf4ff", width=910):
        value = bounded(value, 360)
        if font_file is None:
            # Pillow's bundled subset may lack currency glyphs: retain the explicit GBP label.
            value = value.replace("£", "").replace("—", "-").replace("…", "...")
            value = value.encode("ascii", "replace").decode("ascii")
        while draw.textlength(value, font=font(size)) > width and size > 18:
            size -= 1
        while draw.textlength(value, font=font(size)) > width:
            value = value[:-4] + "..."
        draw.text(xy, value, font=font(size), fill=fill)

    # Original geometric mark, drawn in code; no downloaded logos or fonts.
    draw.polygon(((78, 123), (100, 78), (122, 123), (100, 111)), fill="#52d8e8")
    text("PROJECT ATLAS", (145, 78), size=36)
    text("TRADING RESEARCH", (145, 123), size=20, fill="#9eafc8")
    draw.rounded_rectangle((70, 185, 1010, 240), radius=15, fill="#1a2e46")
    text(event.environment.value + "  /  RESEARCH ONLY", (94, 200), size=24, fill="#73dfed")
    text(notice.title, (70, 278), size=54, fill=notice.accent)
    text(event.instrument_id or "SYSTEM / RESEARCH", (72, 355), size=30, fill="#b9c9e0")
    text(notice.figure_label, (72, 426), size=22, fill="#9eafc8")
    text(notice.figure, (68, 469), size=82, fill=notice.accent)
    draw.line((72, 588, 1008, 588), fill="#2a3a52", width=2)
    relevant = [
        (label, value)
        for label, value in notice.details
        if label not in ("Instrument", "Native currency")
    ]
    # Cards are summaries. Full details, full IDs and missing-value reasons stay in the caption.
    for index, (label, value) in enumerate(relevant[:6]):
        x, y = 72 + 480 * (index % 2), 625 + 90 * (index // 2)
        text(label.upper(), (x, y), size=19, fill="#9eafc8", width=438)
        text(value, (x, y + 30), size=30, width=438)
    text("Strategy: " + (event.strategy or "N/A"), (72, 920), size=23, fill="#b9c9e0")
    text(event.event_at.strftime("%Y-%m-%d  %H:%M:%S UTC"), (72, 957), size=23, fill="#9eafc8")
    text("Event " + event.event_id[:20] + "…", (72, 994), size=20, fill="#9eafc8")
    text(
        "FICTIONAL DESIGN PREVIEW — NOT PERFORMANCE"
        if fictional
        else "CONFIRMED SIMULATION"
        if event.event_type in (EventType.FILL, EventType.PARTIAL)
        else "RESEARCH EVENT — NO BROKER EXECUTION",
        (72, 1040),
        size=20,
        fill=notice.accent,
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
