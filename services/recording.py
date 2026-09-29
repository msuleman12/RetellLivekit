"""Call recording: LiveKit room-composite egress writes an MP3 of both sides of
the call to an S3-compatible bucket. The old build pulled the recording from
Twilio instead; with LiveKit SIP there is no Twilio recording to pull.

`start` runs when the call connects; `finish` runs after hangup and waits for
egress to upload the file, then returns where it is and a time-limited link.
"""

import asyncio
import logging
import time
from typing import Any

from livekit import api as lkapi

from config import LIVEKIT, RECORDING

logger = logging.getLogger("intake.recording")

_DONE = {
    lkapi.EgressStatus.EGRESS_COMPLETE,
    lkapi.EgressStatus.EGRESS_FAILED,
    lkapi.EgressStatus.EGRESS_ABORTED,
    lkapi.EgressStatus.EGRESS_LIMIT_REACHED,
}


def _client() -> lkapi.LiveKitAPI:
    return lkapi.LiveKitAPI(LIVEKIT.url, LIVEKIT.api_key, LIVEKIT.api_secret)


def _s3_client() -> Any:
    import boto3

    return boto3.client(
        "s3",
        region_name=RECORDING.s3_region or None,
        endpoint_url=RECORDING.s3_endpoint or None,
        aws_access_key_id=RECORDING.s3_access_key,
        aws_secret_access_key=RECORDING.s3_secret_key,
    )


async def start(room_name: str) -> str | None:
    """Starts recording the room. Returns the egress id, or None when off or failed."""
    if not RECORDING.enabled:
        return None
    filepath = f"{RECORDING.prefix}{int(time.time())}_{room_name}.mp3"
    request = lkapi.RoomCompositeEgressRequest(
        room_name=room_name,
        audio_only=True,
        file_outputs=[
            lkapi.EncodedFileOutput(
                file_type=lkapi.EncodedFileType.MP3,
                filepath=filepath,
                s3=lkapi.S3Upload(
                    bucket=RECORDING.s3_bucket,
                    region=RECORDING.s3_region,
                    access_key=RECORDING.s3_access_key,
                    secret=RECORDING.s3_secret_key,
                    endpoint=RECORDING.s3_endpoint,
                    force_path_style=bool(RECORDING.s3_endpoint),
                ),
            )
        ],
    )
    try:
        async with _client() as client:
            info = await client.egress.start_room_composite_egress(request)
        logger.info("recording started (egress %s)", info.egress_id)
        return info.egress_id
    except Exception:
        # A recording failure must never affect the call itself.
        logger.exception("could not start call recording")
        return None


async def finish(egress_id: str) -> dict[str, Any]:
    """Stops the egress if it is still running and waits for the upload.

    Returns {status, key, location, url, duration_s, size_bytes}; `url` is a
    presigned link valid for RECORDING_LINK_TTL_S.
    """
    result: dict[str, Any] = {"egress_id": egress_id, "status": "unknown"}
    deadline = time.monotonic() + RECORDING.finish_timeout_s
    async with _client() as client:
        try:
            await client.egress.stop_egress(lkapi.StopEgressRequest(egress_id=egress_id))
        except Exception:
            # Usually already ending because the room was deleted on hangup.
            logger.debug("stop_egress failed; egress probably already ending", exc_info=True)

        info = None
        while time.monotonic() < deadline:
            listed = await client.egress.list_egress(lkapi.ListEgressRequest(egress_id=egress_id))
            info = listed.items[0] if listed.items else None
            if info and info.status in _DONE:
                break
            await asyncio.sleep(2)

    if info is None:
        return result
    result["status"] = lkapi.EgressStatus.Name(info.status).removeprefix("EGRESS_").lower()
    if info.status != lkapi.EgressStatus.EGRESS_COMPLETE or not info.file_results:
        result["error"] = info.error
        logger.warning("recording did not complete: %s %s", result["status"], info.error)
        return result

    file = info.file_results[0]
    result.update(
        key=file.filename,
        location=file.location,
        duration_s=round(file.duration / 1e9, 1),
        size_bytes=file.size,
    )
    try:
        result["url"] = await asyncio.to_thread(
            _s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": RECORDING.s3_bucket, "Key": file.filename},
            ExpiresIn=min(RECORDING.link_ttl_s, 7 * 24 * 3600),
        )
    except Exception:
        logger.exception("could not sign recording link")
    logger.info("recording saved to %s (%.0fs)", file.location, result["duration_s"])
    return result


async def download(key: str) -> bytes:
    def _get() -> bytes:
        return _s3_client().get_object(Bucket=RECORDING.s3_bucket, Key=key)["Body"].read()

    return await asyncio.to_thread(_get)
