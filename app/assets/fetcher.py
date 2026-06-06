from __future__ import annotations

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def fetch_images(urls: list[str], max_bytes: int = 5_242_880) -> list[bytes]:
    results: list[bytes] = []
    total_bytes = 0
    skipped_count = 0
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in urls:
            try:
                response = await client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    skipped_count += 1
                    logger.warning("skipping_non_image", url=url, content_type=content_type)
                    continue

                data = response.content
                if len(data) > max_bytes:
                    skipped_count += 1
                    logger.warning("image_too_large", url=url, size=len(data), max=max_bytes)
                    continue

                results.append(data)
                total_bytes += len(data)
            except Exception as exc:
                skipped_count += 1
                logger.warning("image_fetch_failed", url=url, error=str(exc))
    logger.info(
        "images_fetch_complete",
        requested_count=len(urls),
        fetched_count=len(results),
        skipped_count=skipped_count,
        total_bytes=total_bytes,
    )
    return results
