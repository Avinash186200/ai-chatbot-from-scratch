"""Data ingestion into the point-in-time store.

``sftp_feed`` loads a vendor's daily drop folder of CSV files (an SFTP drop in
production, a local directory in tests and the demo); ``synthetic_feed``
writes a made-up drop folder to exercise it.
"""

from analytics_assistant.ingest.sftp_feed import (
    DropFeedIngester,
    DropFile,
    DropSource,
    FeedConfig,
    FileResult,
    LocalDropSource,
    SFTPDropSource,
)

__all__ = ["DropFeedIngester", "DropFile", "DropSource", "FeedConfig", "FileResult",
           "LocalDropSource", "SFTPDropSource"]
