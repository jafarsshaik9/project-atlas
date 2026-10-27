"""Two-private-chat ownership, persistence and delivery tests; no real Telegram."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import httpx
import pytest

from atlas.activity import EventType, now_utc
from atlas.config import AtlasError
from atlas.dashboard_security import DashboardAuth
from atlas.history import HistoryStore
from atlas.notifications import DeliveryResult, TelegramProvider, TelegramSettings, deliver_one
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs
from atlas.telegram_fanout import Deliveries, RecipientClaimLost, notification_status
from atlas.telegram_recipients import run as admin_cli
from atlas.telegram_recipients_store import ALERTS, Recipients, bot_reference, recipient_mode

TOKEN = "123456:" + "x" * 32
PASSWORD = "local-owner-test-password"
CHATS = ("1000000101", "1000000202")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    store = HistoryStore(tmp_path / "recipients.sqlite3")
    auth = DashboardAuth(store)
    auth.set_password(PASSWORD)
    session = auth.login(PASSWORD, "test-owner")
    registry = Recipients(store)
    return store, auth, session, registry


def enroll(setup, alias="owner", chat=CHATS[0], *, approve=True, **prefs):
    _, _, session, registry = setup
    recipient_id = registry.register_verified(alias, chat, bot_reference(TOKEN), session)
    if approve:
        registry.approve(alias, session, **prefs)
    return recipient_id


def research(store, **kwargs):
    return recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        notify=True,
        **kwargs,
    )


@pytest.fixture
def pair(setup):
    store, _, session, registry = setup
    enroll(setup, synthetic=True, cards=True)
    enroll(setup, "friend", CHATS[1], synthetic=True, cards=True)
    registry.mode(session)
    report, run_id = research(store)
    raw = store.events(run_id, kinds=(EventType.FILL,), descending=False)[0][0]
    return setup, report, raw["event_id"]


def provider(handler, *, cards=False):
    return TelegramProvider(
        TelegramSettings(True, TOKEN, "", True, cards_enabled=cards, multi_recipient=True),
        transport=httpx.MockTransport(handler),
    )


def request_target(request):
    if request.url.path.endswith("/sendPhoto"):
        return re.search(rb'name="chat_id"\r\n\r\n(\d+)', request.read())[1].decode()
    return json.loads(request.content)["chat_id"]


def success(request):
    assert request.url.host == "api.telegram.org"
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def rows(store, event_id):
    with store.connect() as db:
        return [
            dict(r)
            for r in db.execute("SELECT * FROM telegram_deliveries WHERE event_id=?", (event_id,))
        ]


def send(store, sender, event_id, now):
    return deliver_one(store, sender, enabled=True, event_id=event_id, now=now)


def test_verified_chat_remains_pending_until_authenticated_owner_approves(setup):
    store, auth, session, registry = setup
    enroll(setup, approve=False)
    pending = registry.listing(session)[0]
    assert pending["state"] == "PENDING" and not pending["enabled"]
    with pytest.raises(AtlasError, match="authentication"):
        registry.approve("owner", "manually-supplied-approval")
    registry.approve("owner", session)
    approved = registry.listing(session)[0]
    assert approved["enabled"] and not approved["synthetic"] and not approved["cards"]
    auth.logout(session)
    with pytest.raises(AtlasError, match="authentication"):
        registry.disable("owner", session)
    assert not recipient_mode(store)


@pytest.mark.parametrize("chat", ["-10001", "0", "@channel", "True", "1 2", "1" * 21])
def test_registry_accepts_only_verified_positive_private_chat_ids(setup, chat):
    _, _, session, registry = setup
    with pytest.raises(AtlasError, match="private chat"):
        registry.register_verified("friend", chat, bot_reference(TOKEN), session)
    assert registry.listing(session) == []


def test_one_bot_binding_and_duplicate_registration_are_enforced(setup):
    _, _, session, registry = setup
    enroll(setup)
    with pytest.raises(AtlasError, match="already registered"):
        enroll(setup, "another-alias")
    with pytest.raises(AtlasError, match="another bot"):
        registry.register_verified("friend", CHATS[1], bot_reference("654321:" + "x" * 32), session)
    assert len(registry.listing(session)) == 1


def test_expired_verification_cannot_be_approved(setup):
    store, _, session, registry = setup
    enroll(setup, approve=False)
    with store.connect(write=True) as db:
        db.execute(
            "UPDATE telegram_recipients SET verified_at=?",
            ((now_utc() - timedelta(hours=25)).isoformat(),),
        )
    with pytest.raises(AtlasError, match="expired"):
        registry.approve("owner", session)


def test_two_recipient_delivery_restart_dedup_and_portfolio_isolation(pair):
    (store, _, session, registry), report, event_id = pair
    messages = []

    def accept(request):
        messages.append((request_target(request), json.loads(request.content)["text"]))
        return success(request)

    sender = provider(accept)
    now = now_utc()
    assert send(store, sender, event_id, now) == "DELIVERED"
    assert (
        send(HistoryStore(store.path), sender, event_id, now + timedelta(seconds=2)) == "DELIVERED"
    )
    assert send(store, sender, event_id, now + timedelta(seconds=4)) == "NOT_DUE"
    assert {chat for chat, _ in messages} == set(CHATS) and messages[0][1] == messages[1][1]
    assert len(rows(store, event_id)) == len({r["delivery_id"] for r in rows(store, event_id)}) == 2
    assert all(row["state"] == "SENT" for row in rows(store, event_id))
    before = rows(store, event_id)
    repeated, same_id = research(store)
    assert repeated == report and store.run(same_id)["report"] == report.to_dict()
    assert rows(store, event_id) == before
    assert len(Recipients(HistoryStore(store.path)).listing(session)) == 2
    public = json.dumps(notification_status(store))
    assert all(chat not in public for chat in CHATS) and TOKEN not in public
    assert "VERIFIED_RECIPIENTS" in public and registry.delivery_listing(session)


@pytest.mark.parametrize("failure", [403, 429, 503, "timeout"])
def test_one_recipient_failure_never_blocks_the_other(pair, failure):
    (store, _, _, _), _, event_id = pair
    calls = []

    def respond(request):
        chat = request_target(request)
        calls.append(chat)
        if chat == CHATS[0]:
            if failure == "timeout":
                raise httpx.ReadTimeout("private details", request=request)
            return httpx.Response(failure, json={"ok": False, "parameters": {"retry_after": 10}})
        return success(request)

    sender = provider(respond)
    now = now_utc()
    for second in (0, 2):
        send(store, sender, event_id, now + timedelta(seconds=second))
    assert set(calls) == set(CHATS)
    assert sorted(r["state"] for r in rows(store, event_id)) in (
        ["FAILED", "SENT"],
        ["RETRY", "SENT"],
    )


def test_synthetic_delivery_is_disabled_without_both_opt_ins(setup):
    store, _, session, registry = setup
    enroll(setup)  # Approval alone does not subscribe to synthetic research.
    registry.mode(session)
    _, run_id = research(store)
    event_id = store.events(run_id, kinds=(EventType.FILL,))[0][0]["event_id"]
    assert (
        send(store, provider(lambda r: pytest.fail("Unexpected network")), event_id, now_utc())
        == "NOT_DUE"
    )
    assert rows(store, event_id) == []


def test_recipient_alert_categories_are_independent(setup):
    store, _, session, registry = setup
    enroll(setup, synthetic=True, alerts={"risk"})
    enroll(setup, "friend", CHATS[1], synthetic=True, alerts={"trades"})
    registry.mode(session)
    _, run_id = research(store)
    event_id = store.events(run_id, kinds=(EventType.FILL,))[0][0]["event_id"]
    calls = []
    sender = provider(lambda request: (calls.append(request_target(request)), success(request))[1])
    assert send(store, sender, event_id, now_utc()) == "DELIVERED"
    assert calls == [CHATS[1]]


@pytest.mark.parametrize("remove", [False, True])
def test_disable_or_remove_cancels_pending_and_inflight_deliveries(pair, remove):
    (store, _, session, registry), _, event_id = pair
    deliveries = Deliveries(store)
    now = now_utc()
    _, claimed = deliveries.claim("original-worker", event_id=event_id, now=now)
    with store.connect() as db:
        alias = db.execute(
            "SELECT alias FROM telegram_recipients WHERE recipient_id=?", (claimed["recipient_id"],)
        ).fetchone()[0]
    registry.disable(alias, session, remove=remove)
    with pytest.raises(RecipientClaimLost):
        deliveries.media_route(claimed["delivery_id"], "original-worker", 1, True)
    with pytest.raises(RecipientClaimLost):
        deliveries.finish(claimed["delivery_id"], "original-worker", 1, DeliveryResult("DELIVERED"))
    assert send(store, provider(success), event_id, now + timedelta(seconds=2)) == "DELIVERED"
    assert sorted(row["state"] for row in rows(store, event_id)) == ["CANCELLED", "SENT"]
    if remove:
        with store.connect() as db:
            assert (
                db.execute(
                    "SELECT chat_id FROM telegram_recipients WHERE recipient_id=?",
                    (claimed["recipient_id"],),
                ).fetchone()[0]
                is None
            )


def test_new_subscriptions_and_mode_never_backfill_historical_alerts(setup):
    store, _, session, registry = setup
    _, run_id = research(store)
    event_id = store.events(run_id, kinds=(EventType.FILL,))[0][0]["event_id"]
    enroll(setup, synthetic=True)
    registry.mode(session)
    assert (
        send(store, provider(lambda r: pytest.fail("Old event replayed")), event_id, now_utc())
        == "NOT_DUE"
    )
    assert rows(store, event_id) == []


def test_late_friend_does_not_receive_events_queued_before_their_approval(setup):
    store, _, session, registry = setup
    enroll(setup, synthetic=True)
    registry.mode(session)
    _, run_id = research(store)
    event_id = store.events(run_id, kinds=(EventType.FILL,))[0][0]["event_id"]
    enroll(setup, "friend", CHATS[1], synthetic=True)
    calls = []
    sender = provider(lambda request: (calls.append(request_target(request)), success(request))[1])
    now = now_utc()
    assert send(store, sender, event_id, now) == "DELIVERED"
    assert send(store, sender, event_id, now + timedelta(seconds=2)) == "NOT_DUE"
    assert calls == [CHATS[0]]


def test_concurrent_fanout_and_claims_have_unique_recipient_event_pairs(pair):
    (store, _, _, _), _, event_id = pair
    a, b = Deliveries(store), Deliveries(HistoryStore(store.path))
    barrier = Barrier(2)
    now = now_utc()

    def claim(deliveries, owner):
        barrier.wait(timeout=5)
        return deliveries.claim(owner, event_id=event_id, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(claim, a, "one"), pool.submit(claim, b, "two")
        assert (
            sum(value is not None for value in (one.result(timeout=10), two.result(timeout=10)))
            == 1
        )
    assert len(rows(store, event_id)) == 2


def test_each_recipient_can_independently_select_png_or_html(pair):
    (store, _, _, _), _, event_id = pair
    with store.connect(write=True) as db:
        db.execute("UPDATE telegram_recipients SET cards=0 WHERE alias='friend'")
    calls = []
    sender = provider(
        lambda request: (
            calls.append((request_target(request), request.url.path)),
            success(request),
        )[1],
        cards=True,
    )
    now = now_utc()
    for seconds in (0, 2):
        assert send(store, sender, event_id, now + timedelta(seconds=seconds)) == "DELIVERED"
    assert any(chat == CHATS[0] and path.endswith("sendPhoto") for chat, path in calls)
    assert any(chat == CHATS[1] and path.endswith("sendMessage") for chat, path in calls)


def test_uncertain_photo_for_one_recipient_never_suppresses_other_recipient_photo(pair):
    (store, _, _, _), _, event_id = pair
    calls = []

    def respond(request):
        chat = request_target(request)
        calls.append(chat)
        if chat == CHATS[0]:
            raise httpx.ReadTimeout("not logged", request=request)
        return success(request)

    sender = provider(respond, cards=True)
    now = now_utc()
    for second in (0, 2, 100):
        send(store, sender, event_id, now + timedelta(seconds=second))
    assert sorted(calls) == sorted(CHATS)
    assert sorted(r["state"] for r in rows(store, event_id)) == ["FAILED", "SENT"]
    assert {r["media_state"] for r in rows(store, event_id)} == {
        "PHOTO_OUTCOME_UNCERTAIN",
        "PHOTO_DELIVERED",
    }


@pytest.mark.parametrize("known_success", [False, True])
def test_orphaned_photo_receipt_survives_restart_and_setting_changes(pair, known_success):
    (store, _, _, _), _, event_id = pair
    deliveries, now = Deliveries(store), now_utc()
    _, row = deliveries.claim("crashed", event_id=event_id, now=now)
    assert deliveries.media_route(row["delivery_id"], "crashed", 1, True) == "photo"
    if known_success:
        deliveries.media_result(row["delivery_id"], "crashed", 1, "PHOTO_DELIVERED")
    # Finish the other recipient first, then reclaim the crashed one after its lease.
    assert (
        send(store, provider(success, cards=True), event_id, now + timedelta(seconds=2))
        == "DELIVERED"
    )
    sender = provider(lambda request: pytest.fail("Orphaned photo must not be resent"), cards=False)
    result = send(HistoryStore(store.path), sender, event_id, now + timedelta(seconds=31))
    assert result == ("DELIVERED" if known_success else "PHOTO_OUTCOME_UNCERTAIN")


def test_private_opt_out_never_auto_enrolls_or_reenables_start_messages(pair):
    (store, _, session, registry), _, _ = pair
    now = int(now_utc().timestamp())

    def update(index, chat, text):
        return {
            "update_id": index,
            "message": {
                "chat": {"type": "private", "id": int(chat)},
                "from": {"id": int(chat)},
                "text": text,
                "date": now,
            },
        }

    assert (
        registry.opt_out_updates(
            [
                update(1, CHATS[1], "/stop"),
                update(2, "99999", "/start"),
                update(3, CHATS[1], "/start"),
            ]
        )
        == 1
    )
    friend = next(row for row in registry.listing(session) if row["alias"] == "friend")
    assert not friend["enabled"] and len(registry.listing(session)) == 2
    registry.preferences("friend", session, synthetic=True, cards=True, alerts=ALERTS)
    assert Recipients(HistoryStore(store.path)).opt_out_updates([update(1, CHATS[1], "/stop")]) == 0
    assert next(row for row in registry.listing(session) if row["alias"] == "friend")["enabled"]


def test_owner_commands_are_interactive_authenticated_and_do_not_send(setup, monkeypatch, capsys):
    store, _, _, _ = setup
    args = ["--history-db", str(store.path)]
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert admin_cli(["list", *args]) == 1
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "bad-password")
    assert admin_cli(["list", *args]) == 1
    monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD if "owner" in prompt else TOKEN)
    monkeypatch.setattr("atlas.telegram_recipients.discover_private_chat", lambda token: CHATS[0])
    assert admin_cli(["add", "owner", *args]) == 0
    assert admin_cli(["approve", "owner", "--synthetic", "--cards", *args]) == 0
    assert admin_cli(["enable-mode", *args]) == 0
    assert admin_cli(["list", *args]) == 0
    output = capsys.readouterr().out
    assert CHATS[0] not in output and TOKEN not in output and PASSWORD not in output
    assert "PENDING" in output and "nothing sent" in output.lower()


def test_sender_uses_persisted_recipient_mode_without_prompting_chat_id(pair, monkeypatch):
    from atlas.telegram_setup import run

    (store, _, _, _), _, _ = pair
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []

    def prompt(message):
        prompts.append(message)
        return TOKEN

    monkeypatch.setattr("getpass.getpass", prompt)
    monkeypatch.setattr("atlas.telegram_setup.main", lambda args: 0)
    assert run(["--recipients", "--history-db", str(store.path), "--action", "send"]) == 0
    assert len(prompts) == 1 and "token" in prompts[0].lower()


def test_legacy_sender_cannot_bypass_approved_registry(pair):
    (store, _, _, _), _, event_id = pair
    legacy = TelegramProvider(TelegramSettings(True, TOKEN, CHATS[0], True))
    with pytest.raises(AtlasError, match="recipient sender"):
        send(store, legacy, event_id, now_utc())


def test_bot_rotation_uses_same_identity_and_wrong_bot_fails_before_claim(pair):
    (store, _, _, registry), _, event_id = pair
    registry.validate_bot("123456:" + "y" * 32)
    sender = provider(success)
    sender.settings = replace(sender.settings, token="654321:" + "x" * 32)
    with pytest.raises(AtlasError, match="does not match"):
        send(store, sender, event_id, now_utc())
    assert rows(store, event_id) == []


def test_global_disable_does_not_claim_recipient_jobs_or_read_token(pair, monkeypatch):
    (store, _, _, _), _, event_id = pair
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "false")
    config = TelegramSettings.from_environment(allow_synthetic=True, multi_recipient=True)
    assert send(store, TelegramProvider(config), event_id, now_utc()) == "DISABLED"
    assert rows(store, event_id) == []


def test_preference_opt_out_cancels_old_jobs_and_does_not_replay_on_opt_in(pair):
    (store, _, session, registry), _, event_id = pair
    deliveries, now = Deliveries(store), now_utc()
    deliveries.claim("first-worker", event_id=event_id, now=now)
    registry.preferences("friend", session, synthetic=False, cards=False, alerts=set())
    registry.preferences("friend", session, synthetic=True, cards=True, alerts=ALERTS)
    with store.connect() as db:
        friend_id = db.execute(
            "SELECT recipient_id FROM telegram_recipients WHERE alias='friend'"
        ).fetchone()[0]
    assert (
        next(r for r in rows(store, event_id) if r["recipient_id"] == friend_id)["state"]
        == "CANCELLED"
    )
    # No new fanout job appears when the immutable source request is inspected again.
    deliveries.claim("second-worker", event_id=event_id, now=now + timedelta(seconds=31))
    assert len(rows(store, event_id)) == 2


def test_paused_multicast_does_not_fall_back_to_legacy_or_send(pair):
    (store, _, session, registry), _, event_id = pair
    registry.mode(session, paused=True)
    assert recipient_mode(store)
    assert (
        send(
            store,
            provider(lambda r: pytest.fail("Paused sender used network")),
            event_id,
            now_utc(),
        )
        == "NOT_DUE"
    )
    assert notification_status(store)["paused"]
    registry.mode(session)
    assert send(store, provider(success), event_id, now_utc()) == "DELIVERED"


def test_recipient_retries_are_bounded_and_other_recipient_is_not_retried(pair):
    (store, _, _, _), _, event_id = pair
    calls = []

    def respond(request):
        chat = request_target(request)
        calls.append(chat)
        if chat == CHATS[0]:
            raise httpx.ReadTimeout("synthetic lost text response", request=request)
        return success(request)

    sender, now = provider(respond), now_utc()
    for index in range(7):
        send(store, sender, event_id, now + timedelta(seconds=index * 100))
    assert calls.count(CHATS[0]) == 5 and calls.count(CHATS[1]) == 1
    assert sorted(r["state"] for r in rows(store, event_id)) == ["FAILED", "SENT"]


def test_known_photo_rejection_falls_back_for_only_affected_recipient(pair):
    (store, _, _, _), _, event_id = pair
    calls = []

    def respond(request):
        chat = request_target(request)
        method = request.url.path.rsplit("/", 1)[1]
        calls.append((chat, method))
        if chat == CHATS[0] and method == "sendPhoto":
            return httpx.Response(400, json={"ok": False})
        return success(request)

    sender, now = provider(respond, cards=True), now_utc()
    for seconds in (0, 2, 10, 20):
        send(store, sender, event_id, now + timedelta(seconds=seconds))
    assert calls.count((CHATS[0], "sendPhoto")) == 1
    assert calls.count((CHATS[0], "sendMessage")) == 1
    assert calls.count((CHATS[1], "sendPhoto")) == 1
    assert all(r["state"] == "SENT" for r in rows(store, event_id))


def test_delivery_finish_and_audit_are_one_atomic_transaction(pair):
    (store, _, _, _), _, event_id = pair
    deliveries = Deliveries(store)
    _, row = deliveries.claim("worker", event_id=event_id)
    with store.connect(write=True) as db:
        db.execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON telegram_delivery_audit
            BEGIN SELECT RAISE(ABORT, 'synthetic interrupted write'); END""")
    with pytest.raises(AtlasError, match="database operation"):
        deliveries.finish(row["delivery_id"], "worker", 1, DeliveryResult("DELIVERED"))
    assert (
        next(r for r in rows(store, event_id) if r["delivery_id"] == row["delivery_id"])["state"]
        == "SENDING"
    )


def test_recipient_schema_refuses_unknown_versions_without_changes(setup):
    store, _, _, registry = setup
    with store.connect(write=True) as db:
        db.execute("UPDATE telegram_config SET version=99")
    with pytest.raises(AtlasError, match="schema"):
        Recipients(store)
    with store.connect() as db:
        assert db.execute("SELECT version FROM telegram_config").fetchone()[0] == 99


@pytest.mark.parametrize("bad", ["group", "sender_mismatch", "bot", "old", "malformed"])
def test_opt_out_messages_require_current_authenticated_private_sender(pair, bad):
    (_, _, session, registry), _, _ = pair
    message = {
        "chat": {"type": "private", "id": int(CHATS[1])},
        "from": {"id": int(CHATS[1])},
        "text": "/stop",
        "date": int(now_utc().timestamp()),
    }
    if bad == "group":
        message["chat"]["type"] = "group"
    elif bad == "sender_mismatch":
        message["from"]["id"] = 999
    elif bad == "bot":
        message["from"]["is_bot"] = True
    elif bad == "old":
        message["date"] -= 86400
    else:
        message["text"] = {}
    assert registry.opt_out_updates([{"update_id": 1, "message": message}]) == 0
    assert all(r["enabled"] for r in registry.listing(session))
