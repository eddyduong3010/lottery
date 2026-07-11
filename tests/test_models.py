from dataclasses import replace

import pytest
from helpers import make_draw

from xsmn.config import station_from_name


def test_draw_rejects_non_canonical_station_name() -> None:
    with pytest.raises(ValueError, match='không khớp mã'):
        replace(make_draw(), station_name='Sai tên đài')


def test_draw_requires_contiguous_prize_ordinals() -> None:
    draw = make_draw()
    results = list(draw.results)
    index = next(index for index, result in enumerate(results) if result.prize_code == 'g6')
    results[index] = replace(results[index], ordinal=11)

    with pytest.raises(ValueError, match='không liên tục'):
        replace(draw, results=tuple(results))


def test_lam_dong_official_alias_resolves_to_da_lat_station() -> None:
    assert station_from_name('Lâm Đồng').code == 'DL'
