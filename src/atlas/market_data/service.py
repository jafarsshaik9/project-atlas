"""Historical import orchestration, with no execution or activity-history dependencies."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from atlas.config import AtlasError
from atlas.market_data.contracts import DownloadRequest
from atlas.market_data.providers import ProviderError
from atlas.market_data.quality import assess
from atlas.market_data.store import JobOwnershipLost


def download(
    provider,
    request,
    calendar,
    store,
    *,
    refresh=False,
    resume_job_id=None,
    actions=(),
    action_coverage="UNKNOWN",
):
    request = DownloadRequest.model_validate(request.model_dump())
    if action_coverage not in ("UNKNOWN", "PARTIAL", "PROVIDER_DECLARED_COMPLETE"):
        raise AtlasError("Unsupported corporate-action coverage declaration.")
    if request.end > store.clock():
        raise AtlasError("Historical request extends into the future; no import started.")
    descriptor = provider.descriptor
    if calendar.venue != request.instrument.venue:
        raise AtlasError("Historical calendar does not match the instrument venue.")
    zone = ZoneInfo(request.instrument.exchange_timezone)
    day = request.start.astimezone(zone).date()
    last = (request.end - timedelta(microseconds=1)).astimezone(zone).date()
    while day <= last:
        calendar.session(request.instrument.venue, day)
        day += timedelta(days=1)
    if (
        request.provider != descriptor.provider_id
        or request.instrument.venue not in descriptor.exchanges
        or request.resolution not in descriptor.resolutions
    ):
        raise AtlasError("Provider does not support this request.")
    job_id, owner, cached = store.begin_job(
        request,
        calendar.provenance(),
        refresh=refresh,
        resume_job_id=resume_job_id,
        import_context={
            "provider": descriptor.model_dump(mode="json"),
            "actions": [a.model_dump(mode="json") for a in actions],
            "action_coverage": action_coverage,
        },
    )
    if cached:
        store.inspect(cached)
        return cached, job_id, True
    bind_heartbeat = getattr(provider, "set_heartbeat", None)
    if bind_heartbeat:
        bind_heartbeat(lambda: store.heartbeat(job_id, owner))
    try:
        pages = list(store.pages(job_id))
        cursor = store.job(job_id)["cursor"]
        finished = bool(pages) and pages[-1].next_cursor is None
        seen = {page.next_cursor for page in pages if page.next_cursor is not None}
        while not finished:
            if len(pages) >= 64:
                raise ProviderError("PAGE_LIMIT_REQUIRES_SMALLER_REQUEST")
            page = provider.fetch_page(request, cursor)
            if page.next_cursor is not None and (
                page.next_cursor == cursor or page.next_cursor in seen
            ):
                raise ProviderError("PAGINATION_LOOP")
            store.checkpoint(job_id, owner, page)
            pages.append(page)
            cursor = page.next_cursor
            if cursor is None:
                finished = True
            else:
                seen.add(cursor)
        pages = store.pages(job_id)

        def heartbeat():
            store.heartbeat(job_id, owner)

        bars, report = assess(
            request,
            pages,
            calendar,
            actions=actions,
            action_coverage=action_coverage,
            heartbeat=heartbeat,
        )
        heartbeat()
        dataset_id = store.publish(
            job_id,
            owner,
            request,
            descriptor,
            calendar.provenance(),
            bars,
            report,
            pages,
            actions,
            action_coverage,
        )
        return dataset_id, job_id, False
    except JobOwnershipLost:
        # An obsolete worker must not mark a replacement's job interrupted.
        raise
    except (AtlasError, ValueError, OSError) as error:
        code = error.code if isinstance(error, ProviderError) else "IMPORT_NOT_PUBLISHED"
        store.interrupted(job_id, owner, code)
        if isinstance(error, AtlasError):
            error.resume_job_id = job_id
            raise
        failure = AtlasError(
            "Historical import failed; raw checkpoints retained, dataset not published."
        )
        failure.resume_job_id = job_id
        raise failure from None
    finally:
        if bind_heartbeat:
            bind_heartbeat(None)
