import asyncio
import random
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

import psi_chat_bot as m


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    m.cache.clear()
    m.img_cache.clear()
    m.img_locks.clear()
    m._last_proof.clear()
    m._api_usage = None
    monkeypatch.setattr(m, "API_USAGE_FILE", tmp_path / "api_usage.json")
    monkeypatch.setattr(m, "CACHE_FILE", tmp_path / "cache.json")
    random.seed(12345)


# ─────────── Хуеметр ───────────
def test_growth_tables_are_probabilities():
    for vs, ws in [(m.COCK_VALUES, m.COCK_WEIGHTS), (m.W_VALUES, m.W_WEIGHTS),
                   (m.IQ_VALUES, m.IQ_WEIGHTS), (m.H_VALUES, m.H_WEIGHTS)]:
        assert len(vs) == len(ws)
        assert abs(sum(ws) - 1) < 1e-9
        assert all(w >= 0 for w in ws)


def test_cock_growth_step_chances():
    # шаги проходят с шансом 99%, 97%, 95%, ...: P(2 см) / P(1 см) и т.д. по формуле
    w = m.COCK_WEIGHTS
    # P(=k) ∝ reach(k) * (1 - p_k); reach(1)=0.99, reach(2)=0.99*0.97
    assert abs(w[1] / w[2] - (0.99 * 0.03) / (0.99 * 0.97 * 0.05)) < 1e-9
    # 0 см так же редко, как 50 см
    assert w[0] == w[50]
    assert w[0] < 1e-12


def test_cock_distribution():
    n = 200_000
    c = Counter(m.gen_c()[0] for _ in range(n))
    assert min(c) >= 0 and max(c) <= 50
    share = lambda lo: sum(v for k, v in c.items() if k >= lo) / n
    assert 0.32 < share(10) < 0.36     # ожидаем ≈ 34%
    assert 0.07 < share(15) < 0.092    # ожидаем ≈ 8%
    assert 0.006 < share(20) < 0.013   # ожидаем ≈ 0.9%
    assert share(30) < 0.0005          # практически никогда


def test_every_cock_value_has_emoji():
    assert all(m._emo(v, m.EMO["c"]) for v in range(51))


@pytest.mark.parametrize("gen, lo, hi, table", [
    (m.gen_w, 0, 250, "w"), (m.gen_iq, 50, 200, "iq"), (m.gen_h, 140, 220, "h"),
])
def test_other_generators_in_range_with_emoji(gen, lo, hi, table):
    for _ in range(20_000):
        v, e = gen()
        assert lo <= v <= hi
        assert e == m._emo(v, m.EMO[table]) and e


@pytest.mark.parametrize("gen, values, weights", [
    (m.gen_w, m.W_VALUES, m.W_WEIGHTS), (m.gen_iq, m.IQ_VALUES, m.IQ_WEIGHTS),
    (m.gen_h, m.H_VALUES, m.H_WEIGHTS),
])
def test_other_generators_follow_table(gen, values, weights):
    n = 100_000
    xs = [gen()[0] for _ in range(n)]
    # медиана по таблице: половина результатов не больше её
    acc = 0
    for v, w in zip(values, weights):
        acc += w
        if acc >= 0.5:
            med = v
            break
    below = sum(1 for x in xs if x <= med) / n
    expected = sum(w for v, w in zip(values, weights) if v <= med)
    assert abs(below - expected) < 0.01


# ─────────── Кэш ───────────
def test_cached_val_is_stable_and_pruned():
    async def go():
        a = await m.cached_val(1, "cock")
        b = await m.cached_val(1, "cock")
        assert a == b
        m.cache["cock_2"] = (datetime.now() - timedelta(hours=7), 5, "")
        await m.prune_caches()
        assert "cock_1" in m.cache and "cock_2" not in m.cache
        await asyncio.sleep(0)  # дать отработать фоновому сохранению
    run(go())


# ─────────── Лимиты ───────────
def test_daily_quota(monkeypatch):
    monkeypatch.setitem(m.DAILY_LIMITS, "proof", 2)
    async def go():
        assert await m.consume_daily_quota("proof")
        assert await m.consume_daily_quota("proof")
        assert not await m.consume_daily_quota("proof")
        assert await m.consume_daily_quota("image")  # отдельный счётчик
    run(go())
    assert m.API_USAGE_FILE.exists()


def test_old_usage_file_format_is_accepted(monkeypatch):
    today = m.datetime.now(m.timezone.utc).strftime("%Y-%m-%d")
    m.API_USAGE_FILE.write_text(f'{{"date": "{today}", "count": 99}}')
    assert run(m.consume_daily_quota("proof"))


def test_proof_cooldown():
    assert m.proof_cooldown_left(7) == 0
    assert m.proof_cooldown_left(7) > 0
    m._last_proof[7] = datetime.now() - m.PROOF_COOLDOWN - timedelta(seconds=1)
    assert m.proof_cooldown_left(7) == 0


# ─────────── Форматирование /proof ───────────
def test_md_to_tg_html():
    src = "## Вердикт\n**Неправда**: a<b & c\n- пункт *важный*\n[сайт](https://x.org/?a=1&b=2)"
    out = m.md_to_tg_html(src)
    assert "<b>Вердикт</b>" in out
    assert "<b>Неправда</b>: a&lt;b &amp; c" in out
    assert "• пункт <i>важный</i>" in out
    assert '<a href="https://x.org/?a=1&amp;b=2">сайт</a>' in out


def test_format_sources_escapes():
    out = m.format_sources([("A&B", 'https://e.com/?q="x"')])
    assert "A&amp;B" in out and "&quot;x&quot;" in out
    assert m.format_sources([]) == ""


def test_extract_sources_dedup_and_limit():
    chunks = [{"web": {"uri": f"https://r/{i}", "title": f"site{i % 7}"}} for i in range(20)]
    src = m.extract_sources({"groundingMetadata": {"groundingChunks": chunks}})
    assert len(src) == m.MAX_SOURCES
    assert len({t for t, _ in src}) == len(src)
    assert m.extract_sources({}) == []


def test_html_to_plain():
    assert m.html_to_plain("<b>a&lt;b</b> <a href='x'>c</a>") == "a<b c"


def test_split_message():
    t = "a" * 3000 + "\n\n" + "<b>" + "b" * 3000 + "</b>\n" + "c" * 5000
    chunks = m.split_message(t)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks).replace("\n", "") == t.replace("\n", "")
    assert m.split_message("short") == ["short"]


# ─────────── «Хто Я?» ───────────
def _cb(calls, uid=42):
    async def answer(text=None):
        calls["answers"].append(text)
    return NS(from_user=NS(id=uid, full_name="Test", username=None),
              message=NS(chat=NS(id=-1)), data="whoami", answer=answer)


def test_whoami_double_press_and_regeneration(monkeypatch):
    calls = {"gen": 0, "sent": [], "answers": []}

    async def fake_make_image(ctx):
        calls["gen"] += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("no gemini")

    async def fake_send_photo(chat_id, photo, caption=None, parse_mode=None):
        calls["sent"].append(caption)

    monkeypatch.setattr(m, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(m, "make_image", fake_make_image)
    monkeypatch.setattr(m.bot, "send_photo", fake_send_photo)

    async def go():
        await asyncio.gather(m.callbacks(_cb(calls)), m.callbacks(_cb(calls)))
        assert calls["gen"] == 1                 # двойное нажатие — одна генерация
        assert len(calls["sent"]) == 2
        assert calls["answers"] == ["Рисую портрет…"] * 2
        ts, v, e = m.cache["cock_42"]
        m.cache["cock_42"] = (ts, v + 1, e)      # цифры поменялись — картинку перерисовать
        await m.callbacks(_cb(calls))
        assert calls["gen"] == 2
    run(go())


def test_whoami_respects_image_quota(monkeypatch):
    calls = {"gen": 0, "sent": [], "answers": []}

    async def fake_make_image(ctx):
        calls["gen"] += 1
        raise AssertionError("не должен вызываться при исчерпанном лимите")

    async def fake_send_photo(chat_id, photo, caption=None, parse_mode=None):
        calls["sent"].append(caption)

    monkeypatch.setattr(m, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(m, "make_image", fake_make_image)
    monkeypatch.setattr(m.bot, "send_photo", fake_send_photo)
    monkeypatch.setitem(m.DAILY_LIMITS, "image", 0)

    run(m.callbacks(_cb(calls)))
    assert calls["gen"] == 0 and len(calls["sent"]) == 1   # отправлена PIL-картинка


# ─────────── Команды ───────────
def _msg(replies, uid=1, username="vasya", reply_to=None):
    async def reply(text, **kw):
        replies.append(text)
    user = NS(id=uid, username=username, full_name="Вася")
    return NS(from_user=user, reply_to_message=reply_to, reply=reply)


def test_pizdica_self_duel_rejected():
    replies = []
    me = NS(id=1, username="vasya", full_name="Вася")
    run(m.cmd_pizdica(_msg(replies, reply_to=NS(from_user=me)), NS(args=None)))
    run(m.cmd_pizdica(_msg(replies), NS(args="@VASYA")))
    assert all("С самим собой" in r for r in replies) and len(replies) == 2


def test_pizdica_normal_duel():
    replies = []
    run(m.cmd_pizdica(_msg(replies), NS(args="@petya")))
    assert "Победитель" in replies[0]


def test_cock_command_matches_cache():
    replies = []
    run(m.cmd_cock(_msg(replies, uid=5)))
    v, e = run(m.cached_val(5, "cock"))
    assert replies == [f"Вася, ваш хуй: {v} см {e}"]


# ─────────── /proof ───────────
class _ProofMsg:
    def __init__(self, uid=10):
        self.from_user = NS(id=uid, username="u", full_name="U")
        self.message_id = 1
        self.chat = NS(id=-1)
        self.quote = None
        self.reply_to_message = None
        self.replies, self.answers, self.deleted = [], [], 0

    async def reply(self, text, **kw):
        self.replies.append(text)
        msg = self

        class _Proc:
            async def delete(self_inner):
                msg.deleted += 1
        return _Proc()

    async def answer(self, text, **kw):
        self.answers.append((text, kw.get("parse_mode")))


def test_proof_success_formats_html_and_sources(monkeypatch):
    async def fake(session, text):
        return True, "**Неправда**: 2<3", [("wiki", "https://w.org/a")]
    monkeypatch.setattr(m, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(m, "proof_with_gemini", fake)
    msg = _ProofMsg()
    run(m.proof_command_handler(msg, NS(args="земля плоская, это доказано")))
    text, mode = msg.answers[0]
    assert "<b>Неправда</b>: 2&lt;3" in text and '<a href="https://w.org/a">wiki</a>' in text
    assert msg.deleted == 1


def test_proof_crash_cleans_up_and_reports(monkeypatch):
    async def boom(session, text):
        raise RuntimeError("сеть упала")
    monkeypatch.setattr(m, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(m, "proof_with_gemini", boom)
    msg = _ProofMsg()
    run(m.proof_command_handler(msg, NS(args="земля плоская, это доказано")))
    assert msg.deleted == 1                                  # «Ищу и проверяю…» убрано
    assert msg.replies[-1].startswith("Не удалось выполнить проверку")


def test_proof_cooldown_blocks_second_call(monkeypatch):
    async def fake(session, text):
        return True, "ok", []
    monkeypatch.setattr(m, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(m, "proof_with_gemini", fake)
    msg = _ProofMsg(uid=99)
    run(m.proof_command_handler(msg, NS(args="первая проверка текста")))
    run(m.proof_command_handler(msg, NS(args="вторая проверка текста")))
    assert msg.replies[-1].startswith("Подождите")
    assert len(msg.answers) == 1
