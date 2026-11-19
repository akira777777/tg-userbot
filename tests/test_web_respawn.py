"""Решение о перезапуске watch: только аварийный код, не ручной стоп."""

import web


def test_respawn_watch_after_crash():
    assert web._respawn_after("watch", 1, killed=False, enabled=True) == 30.0


def test_respawn_watch_after_force_kill_code():
    assert web._respawn_after("watch", -1, killed=False, enabled=True) == 30.0


def test_no_respawn_when_user_killed():
    assert web._respawn_after("watch", -1, killed=True, enabled=True) is None


def test_no_respawn_for_clean_exit_or_other_jobs():
    assert web._respawn_after("watch", 0, killed=False, enabled=True) is None
    assert web._respawn_after("sync", 1, killed=False, enabled=True) is None


def test_no_respawn_when_disabled():
    assert web._respawn_after("watch", 1, killed=False, enabled=False) is None



# ==========================================================================
# Lifecycle-тесты автоперезапуска watch: проводка BUS.jobs и отменяемость.
#
# Покрывают два критических дефекта ревью:
#   1. _respawn() звал run_job без регистрации записи в BUS.jobs — гард в
#      run_job ("слот уже перешёл другой задаче") убивал поднятый процесс
#      сразу после спавна;
#   2. в окне ожидания респаун мог перезаписать/удалить запись ручного
#      старта (name='watch' совпадает), а ручной стоп не отменял отложенный
#      респаун.
# ==========================================================================

import asyncio
import itertools

import pytest



class _FakeStream:
    """stdout фейкового процесса: мгновенный EOF либо ожидание gate."""

    def __init__(self, gate=None):
        self.gate = gate

    async def readline(self):
        if self.gate is not None:
            await self.gate.wait()
        return b""


class _FakeProc:
    _ids = itertools.count(9000)

    def __init__(self, returncode=1, gate=None):
        self.pid = next(_FakeProc._ids)
        self.returncode = returncode
        self.stdout = _FakeStream(gate)
        self.killed = False

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


class _Spawner:
    """Фейк web.asyncio.create_subprocess_exec.

    codes — очередь exit-кодов бот-процессов по порядку спавнов (дефолт 0);
    block — индексы бот-спавнов, чей stdout висит до env.gate.set().
    taskkill-спавны (_kill_tree на Windows) всегда возвращают код 0.
    """

    def __init__(self, codes=(), block=()):
        self.codes = list(codes)
        self.block = set(block)
        self.gate = asyncio.Event()
        self.bot_procs = []
        self.argv = []

    async def __call__(self, *args, **kwargs):
        self.argv.append(args)
        if args and str(args[0]) == "taskkill":
            return _FakeProc(returncode=0)
        idx = len(self.bot_procs)
        code = self.codes[idx] if idx < len(self.codes) else 0
        proc = _FakeProc(returncode=code,
                         gate=self.gate if idx in self.block else None)
        self.bot_procs.append(proc)
        return proc


class _FakeLocker:
    """In-memory SlotLocker: acquire/release по множеству имён."""

    def __init__(self):
        self.held = set()

    async def acquire(self, name):
        if name in self.held:
            return False
        self.held.add(name)
        return True

    async def release(self, name):
        self.held.discard(name)


class _TestBus(web.Bus):
    """Шина без записи в web.log: события копятся в .events."""

    def __init__(self):
        super().__init__()
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


class _FakeReq:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def env(monkeypatch):
    """Свежие BUS/RUN_LOCK/слоты/счётчики и фейковый subprocess на тест.

    НЕ autouse: оригинальные 5 тестов _respawn_after проверяют константу
    _RESPAWN_DELAY_SECONDS == 30.0 и не должны видеть подменённое окружение.
    """
    spawner = _Spawner()
    monkeypatch.setattr(web, "BUS", _TestBus())
    monkeypatch.setattr(web, "SLOT_LOCKS", _FakeLocker())
    monkeypatch.setattr(web, "RUN_LOCK", asyncio.Lock())
    # raising=False: атрибута _respawn_tasks ещё нет на текущем коде — RED
    # должен падать по поведению, а не в setup фикстуры
    monkeypatch.setattr(web, "_respawn_tasks", {}, raising=False)
    monkeypatch.setattr(web, "_watch_restarts", {}, raising=False)
    monkeypatch.setattr(web, "_RESPAWN_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(web.asyncio, "create_subprocess_exec", spawner)
    return spawner


async def _wait_until(pred, timeout=2.0, what="условие"):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError(f"таймаут ожидания: {what}")
        await asyncio.sleep(0.01)


def _try_watch(account):
    return web.try_start({"action": "watch", "account": account})


# --------------------------------------------------------------------------
# ДЕФЕКТ 1: автоперезапуск поднимает процесс и регистрирует его в BUS
# --------------------------------------------------------------------------

def test_crashed_watch_is_respawned_and_registered(env):
    env.codes = [1]      # первый спавн аварийно падает
    env.block = {1}      # респавненный процесс остаётся живым для проверки

    async def scenario():
        code, payload = await _try_watch("acc_one")
        assert code == 200, payload
        await _wait_until(
            lambda: len(env.bot_procs) == 1 and "acc_one" not in web.BUS.jobs,
            what="крах: процесс завершился, запись вычищена")
        await _wait_until(
            lambda: len(env.bot_procs) >= 2
            and web.BUS.jobs.get("acc_one") is not None
            and web.BUS.jobs["acc_one"].get("proc") is not None,
            timeout=3.0, what="респавн: второй спавн зарегистрирован в BUS")
        job = web.BUS.jobs["acc_one"]
        assert job["name"] == "watch"
        assert job["proc"] is env.bot_procs[1]
        assert job["pid"] == env.bot_procs[1].pid
        # ДЕФЕКТ 1: гард в run_job убивал респавненный процесс сразу после спавна
        assert env.bot_procs[1].killed is False

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# ДЕФЕКТ 2: ручной старт/стоп в окне ожидания выигрывает у респауна
# --------------------------------------------------------------------------

def test_manual_start_during_window_wins(env):
    env.codes = [1, 0]
    env.block = {1}      # ручной процесс пользователя остаётся живым

    async def scenario():
        code, _ = await _try_watch("acc_two")
        assert code == 200
        await _wait_until(
            lambda: len(env.bot_procs) == 1 and "acc_two" not in web.BUS.jobs,
            what="крах первичного процесса")
        # окно ожидания респауна (~0.05с): пользователь запускает watch вручную
        await asyncio.sleep(0.02)
        code2, payload2 = await _try_watch("acc_two")
        assert code2 == 200, payload2
        user_job = web.BUS.jobs["acc_two"]
        # дождаться спавн ручного процесса (try_start возвращается раньше него)
        await _wait_until(lambda: len(env.bot_procs) >= 2,
                          what="спавн ручного старта")
        user_proc = env.bot_procs[1]
        # ждём, пока «проснувшийся» респаун успел бы навредить
        await asyncio.sleep(0.2)
        assert web.BUS.jobs.get("acc_two") is user_job,             "респаун перезаписал или удалил запись ручного старта"
        assert user_job["pid"] == user_proc.pid
        assert len(env.bot_procs) == 2, "респаун поднял лишний процесс на чужом слоте"
        assert user_proc.killed is False

    asyncio.run(scenario())


def test_manual_stop_cancels_pending_respawn(env):
    env.codes = [1]

    async def scenario():
        code, _ = await _try_watch("acc_three")
        assert code == 200
        await _wait_until(
            lambda: len(env.bot_procs) == 1 and "acc_three" not in web.BUS.jobs,
            what="крах и планирование респауна")
        resp = await web.api_stop(_FakeReq({"account": "acc_three"}))
        assert resp.status == 200
        # окно респауна давно истекло — нового спавна быть не должно
        await asyncio.sleep(0.2)
        assert len(env.bot_procs) == 1, "респаун не был отменён ручным стопом"
        assert web._respawn_tasks.get("acc_three") is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Регрессии: стоп не ломается, счётчик рестартов держит лимит
# --------------------------------------------------------------------------

def test_api_stop_sets_kill_flag(env):
    env.codes = [0]
    env.block = {0}

    async def scenario():
        code, _ = await _try_watch("acc_four")
        assert code == 200
        await _wait_until(
            lambda: web.BUS.jobs.get("acc_four") is not None
            and web.BUS.jobs["acc_four"].get("proc") is not None,
            what="живой процесс зарегистрирован")
        resp = await web.api_stop(_FakeReq({"account": "acc_four"}))
        assert resp.status == 200
        assert web.BUS.jobs["acc_four"]["kill"] is True
        # отложенного респауна для слота нет — стоп ничего не провоцирует
        assert getattr(web, "_respawn_tasks", {}).get("acc_four") is None

    asyncio.run(scenario())


def test_restart_counter_caps_and_resets(env):
    env.codes = [1] * 6 + [0, 1]

    async def scenario():
        code, _ = await _try_watch("acc_five")
        assert code == 200
        # 6 подряд крашей: первые 5 планируют респаун (цепочка спавнов),
        # на 6-м счётчик достигает лимита — новый респаун не планируется.
        await _wait_until(lambda: len(env.bot_procs) == 6,
                          timeout=3.0, what="6 крашей подряд")
        await asyncio.sleep(0.15)
        assert len(env.bot_procs) == 6, "респаун запланирован сверх лимита"
        assert web._watch_restarts.get("acc_five") == 5
        assert web._respawn_tasks == {}
        # чистый выход (код 0) сбрасывает счётчик
        code2, _ = await _try_watch("acc_five")
        assert code2 == 200
        await _wait_until(
            lambda: "acc_five" not in web.BUS.jobs and len(env.bot_procs) == 7,
            what="чистый выход")
        assert web._watch_restarts.get("acc_five") is None
        # следующий крах снова планирует респаун
        code3, _ = await _try_watch("acc_five")
        assert code3 == 200
        await _wait_until(
            lambda: "acc_five" not in web.BUS.jobs and len(env.bot_procs) == 8,
            what="крах после сброса счётчика")
        assert web._watch_restarts.get("acc_five") == 1
        assert "acc_five" in web._respawn_tasks
        # добиваем: ручной стоп отменяет хвостовой респаун
        await web.api_stop(_FakeReq({"account": "acc_five"}))
        await asyncio.sleep(0.15)
        assert len(env.bot_procs) == 8

    asyncio.run(scenario())
