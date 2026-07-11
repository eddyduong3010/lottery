from datetime import datetime

from xsmn.calendar import VIETNAM_TZ, next_draw_date, next_regional_draw_date, stations_for_date


def test_schedule_and_cutoff() -> None:
    before_draw = datetime(2026, 7, 10, 15, 0, tzinfo=VIETNAM_TZ)
    after_draw = datetime(2026, 7, 10, 17, 0, tzinfo=VIETNAM_TZ)

    assert stations_for_date(before_draw.date()) == ('VL', 'BD', 'TV')
    assert next_regional_draw_date(before_draw).isoformat() == '2026-07-10'
    assert next_regional_draw_date(after_draw).isoformat() == '2026-07-11'
    assert next_draw_date('VL', after_draw).isoformat() == '2026-07-17'
