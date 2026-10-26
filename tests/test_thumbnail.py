import pytest
from PIL import Image

from subtitleshort.config import ConfigError
from subtitleshort.fonts import find_font
from subtitleshort.thumbnail import Cover, _Panel, compose, load_emoji, sheet_times


def test_sheet_times_are_spread_inside_the_video():
    times = sheet_times(60.0)
    assert len(times) == 12 and times == sorted(times)
    assert 0 < times[0] < 5 and 55 < times[-1] < 60


def test_emojis_by_name_or_character():
    assert load_emoji("calavera").size == load_emoji("💀").size == (512, 512)
    with pytest.raises(ConfigError, match="Disponibles"):
        load_emoji("unicornio")


def test_panel_maps_points_from_frame_to_cover():
    panel = _Panel((100, 0, 300, 200), (0, 0, 1080, 1080))
    assert panel.map(200, 100) == (540, 540)
    assert panel.map(50, 100) is None


@pytest.mark.parametrize("size, cover", [
    ((1080, 1920), Cover("MI PLAN|*PERFECTO*", face=(50, 15), emojis=("calavera", "risa@16,88"),
                         arrow=(60, 70), circle=(40, 80))),
    ((1920, 1080), Cover("¿SWITCH 2|*DÉBIL*?", red=False)),  # landscape source, no facecam
])
def test_compose_always_returns_a_vertical_cover(size, cover):
    frame = Image.linear_gradient("L").resize(size).convert("RGB")
    image = compose(frame, cover, find_font("Montserrat-Black.ttf").path)
    assert image.size == (1080, 1920) and image.mode == "RGB"
