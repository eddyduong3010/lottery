from datetime import date

import pytest

from vietlott_power655.analytics import frequency_statistics, prize_probabilities
from vietlott_power655.models import Power655Draw, PrizeTierResult
from vietlott_power655.prediction import generate_ticket_candidates
from vietlott_power655.prize_checker import check_ticket, parse_ticket_numbers
from vietlott_power655.repository import SQLiteRepository
from vietlott_power655.scraper import (
    VietlottPower655Client,
    parse_detail_html,
    parse_history_html,
    parse_xoso_history_html,
)


def make_power_draw(draw_id: str = '01370') -> Power655Draw:
    return Power655Draw(
        draw_id=draw_id,
        draw_date=date(2026, 7, 11),
        main_numbers=('09', '17', '20', '33', '41', '42'),
        bonus_number='40',
        prizes=(
            PrizeTierResult('jackpot1', 1, 102_862_626_150),
            PrizeTierResult('jackpot2', 1, 4_564_759_400),
            PrizeTierResult('first', 23, 40_000_000),
            PrizeTierResult('second', 1856, 500_000),
            PrizeTierResult('third', 31612, 50_000),
        ),
        source_url='https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/655?id=01370&nocatche=1',
    )


def test_power655_model_rejects_duplicate_numbers() -> None:
    with pytest.raises(ValueError, match='không được trùng'):
        Power655Draw(
            draw_id='1',
            draw_date=date(2026, 7, 11),
            main_numbers=('01', '01', '02', '03', '04', '05'),
            bonus_number='06',
            prizes=make_power_draw().prizes,
        )


def test_power655_ticket_checker_handles_jackpot2_and_fixed_prize() -> None:
    draw = make_power_draw()

    jackpot2 = check_ticket('09 17 20 33 41 40', draw)
    assert jackpot2.is_winner
    assert jackpot2.hit is not None
    assert jackpot2.hit.tier_code == 'jackpot2'
    assert jackpot2.hit.payout_vnd == 4_564_759_400

    first = check_ticket('09,17,20,33,41,01', draw)
    assert first.hit is not None
    assert first.hit.tier_code == 'first'
    assert first.hit.payout_vnd == 40_000_000


def test_power655_ticket_parser_validates_six_unique_numbers() -> None:
    assert parse_ticket_numbers('9-17-20-33-41-42') == ('09', '17', '20', '33', '41', '42')
    with pytest.raises(ValueError, match='không được trùng'):
        parse_ticket_numbers('09 09 20 33 41 42')


def test_parse_power655_history_html() -> None:
    html = """
    <div id="divResultContent"><table><tbody>
      <tr>
        <td>11/07/2026</td>
        <td><a href="/vi/trung-thuong/ket-qua-trung-thuong/655?id=01370&nocatche=1">01370</a></td>
        <td><span class="bong_tron">09</span><span class="bong_tron">17</span><span class="bong_tron">20</span>
        <span class="bong_tron">33</span><span class="bong_tron">41</span><span class="bong_tron">42</span>
        <span class="bong_tron-sperator">|</span><span class="bong_tron">40</span></td>
      </tr>
    </tbody></table></div>
    """
    draws = parse_history_html(html)
    assert len(draws) == 1
    assert draws[0].draw_id == '01370'
    assert draws[0].main_numbers == ('09', '17', '20', '33', '41', '42')
    assert draws[0].bonus_number == '40'


def test_parse_power655_ajax_fragment_and_metadata() -> None:
    html = """
    <script>AjaxOut = X.ServerSideDrawResult(RenderInfo, 'history-key', '', [], false, 0);</script>
    <select id="drpSelectGameDraw"><option value="">Toàn bộ</option><option value="a">Kỳ 2</option>
    <option value="b">Kỳ 1</option></select>
    <table><tbody><tr>
      <td>11/07/2026</td><td><a href="/655?id=01370">01370</a></td>
      <td><span class="bong_tron">09</span><span class="bong_tron">17</span>
      <span class="bong_tron">20</span><span class="bong_tron">33</span>
      <span class="bong_tron">41</span><span class="bong_tron">42</span>
      <span class="bong_tron">40</span></td>
    </tr></tbody></table>
    """
    assert VietlottPower655Client._history_key(html) == 'history-key'
    assert VietlottPower655Client._history_draw_count(html) == 2
    assert parse_history_html(html)[0].draw_id == '01370'


def test_parse_xoso_power655_fallback_with_prizes() -> None:
    html = """
    <section class="section">
      <header><h2>Xổ số Power 6/55, <a href="/xo-so-power-655-ngay-11-07-2026.html">11/07/2026</a></h2></header>
      <div class="mega-results">
        <div class="jackpot-item">Kỳ quay thưởng: <strong>#01370</strong></div>
        <div class="jackpot-item"><span class="btn-results">09</span><span class="btn-results">17</span>
        <span class="btn-results">20</span><span class="btn-results">33</span><span class="btn-results">41</span>
        <span class="btn-results">42</span><span class="btn-results bg_jackpot">40</span></div>
      </div>
      <table><tbody>
        <tr><td>Jackpot 1</td><td></td><td>1</td><td>102.862.626.150</td></tr>
        <tr><td>Jackpot 2</td><td></td><td>1</td><td>4.564.759.400</td></tr>
        <tr><td>Giải nhất</td><td></td><td>23</td><td>40.000.000</td></tr>
        <tr><td>Giải nhì</td><td></td><td>1.856</td><td>500.000</td></tr>
        <tr><td>Giải ba</td><td></td><td>31.612</td><td>50.000</td></tr>
      </tbody></table>
    </section>
    """
    draws = parse_xoso_history_html(html)

    assert len(draws) == 1
    assert draws[0].draw_id == '01370'
    assert draws[0].main_numbers == ('09', '17', '20', '33', '41', '42')
    assert draws[0].bonus_number == '40'
    assert draws[0].prizes[0].payout_vnd == 102_862_626_150


def test_parse_power655_detail_html_with_prizes() -> None:
    html = """
    <div class="chitietketqua_title"><h5>Kỳ quay thưởng <b>#01370</b> ngày <b>11/07/2026</b></h5></div>
    <div class="day_so_ket_qua"><div class="day_so_ket_qua_v2">
      <span class="bong_tron small">09</span><span class="bong_tron small">17</span>
      <span class="bong_tron small">20</span><span class="bong_tron small">33</span>
      <span class="bong_tron small">41</span><span class="bong_tron small">42</span>
      <span class="bong_tron small active">40</span>
    </div></div>
    <div class="chitietketqua_table"><table><tbody>
      <tr><td>Jackpot 1</td><td></td><td>1</td><td>102.862.626.150</td></tr>
      <tr><td>Jackpot 2</td><td></td><td>1</td><td>4.564.759.400</td></tr>
      <tr><td>Giải Nhất</td><td></td><td>23</td><td>40.000.000</td></tr>
      <tr><td>Giải Nhì</td><td></td><td>1.856</td><td>500.000</td></tr>
      <tr><td>Giải Ba</td><td></td><td>31.612</td><td>50.000</td></tr>
    </tbody></table></div>
    """
    draw = parse_detail_html(html)
    assert draw.draw_id == '01370'
    assert draw.prizes[0].payout_vnd == 102_862_626_150
    assert draw.prizes[-1].winner_count == 31_612


def test_power655_repository_roundtrip(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / 'power655.sqlite3')
    repository.upsert_draw(make_power_draw())
    repository.upsert_draw(make_power_draw())

    loaded = repository.get_draw('01370')
    assert loaded is not None
    assert loaded.main_numbers[0] == '09'
    assert len(repository.load_draws()) == 1


def test_bulk_history_does_not_overwrite_existing_prize_details(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / 'power655.sqlite3')
    detailed = make_power_draw()
    repository.upsert_draw(detailed)
    history_only = Power655Draw(
        draw_id=detailed.draw_id,
        draw_date=detailed.draw_date,
        main_numbers=detailed.main_numbers,
        bonus_number=detailed.bonus_number,
        prizes=tuple(
            PrizeTierResult(prize.tier_code, None, prize.payout_vnd if prize.tier_code != 'jackpot1' else None)
            for prize in detailed.prizes
        ),
        source_url=detailed.source_url,
    )
    repository.upsert_draw(history_only)

    loaded = repository.get_draw(detailed.draw_id)
    assert loaded is not None
    assert loaded.prizes[0].winner_count == 1
    assert loaded.prizes[0].payout_vnd == 102_862_626_150


def test_power655_theoretical_probabilities() -> None:
    probabilities = prize_probabilities().set_index('prize')
    assert probabilities.loc['Jackpot 1', 'odds_one_in'] == 28_989_675
    assert probabilities.loc['Jackpot 2', 'winning_combinations'] == 6
    assert probabilities.loc['Bất kỳ giải nào', 'probability'] > probabilities.loc['Giải Ba', 'probability']


def test_power655_analytics_and_prediction_do_not_mutate_draws(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / 'power655-prediction.sqlite3')
    draws = [
        make_power_draw('01370'),
        Power655Draw(
            draw_id='01371',
            draw_date=date(2026, 7, 14),
            main_numbers=('01', '02', '03', '04', '05', '06'),
            bonus_number='07',
            prizes=make_power_draw().prizes,
        ),
    ]
    repository.upsert_draws(draws)
    frame = repository.load_draws()

    stats = frequency_statistics(frame)
    assert stats.loc[stats['number'] == '09', 'count'].iloc[0] == 1
    candidates = generate_ticket_candidates(frame, date(2026, 7, 16), candidate_count=2, recent_draws=2)
    assert len(candidates) == 2
    assert all(len(numbers.split()) == 6 for numbers in candidates['numbers'])
